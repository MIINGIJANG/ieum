"""전체 흐름: 질문 → KOSHA 검색 → 쉬운 문장 → 수어 글로스 → (동작사전) → 아바타용 동작.

번역 메모리(demo_sentences.json)
  조원이 글로스·키포인트를 확정한 안내 문장들. 입력 문장이 여기 있으면 모델 대신 확정 글로스를 쓴다.
  (예제 기반 번역 / 번역 메모리 방식 — 자주 쓰는 안전 문구는 검수된 결과를 그대로 쓰는 게 안전)
  질문 모드에서는 질문과 가장 비슷한 안내 문장을 골라 답한다(유사도가 기준 이상일 때).
"""
import json
import re
from pathlib import Path

import numpy as np

from .motion import MotionDict, compose
from .rag import SafetyRAG
from .translate import Translator, split_sentences

_norm = lambda t: re.sub(r"[\s.,!?·]", "", t)
# 문단 앞 번호 표시: 1.  (1)  ①  가.  (가)  -  ·  O
_MARK = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|\(\d{1,2}\)|[\u2460-\u2473]|\(?[가-하][.)]|\([가-하]\)|[-·•O○]\s)\s*")


_HEADNUM = re.compile(r"^\d{1,2}(?=[가-힣])(?!(?:미터|시간|개월|년|월|일|명|회|종|층|개|번|차|분|초))")   # '2안전화의 종류' 같은 절 번호


def strip_marks(text):
    return "\n".join(_HEADNUM.sub("", _MARK.sub("", ln)).strip() for ln in text.split("\n") if ln.strip())


def merge_sources(hits, titles, order=None, tables=None, images=None):
    """같은 문서·쪽은 하나로 합치고(겹치는 문장 제거, 문서 순서대로) 번호 표시를 뗀 전체 문장으로."""
    out = {}
    rank = {}
    for i, h in enumerate(hits):                      # 쪽 안의 문장은 문서 순
        rank.setdefault((h["doc"], h["page"]), i)
    order = order or {}
    for h in sorted(hits, key=lambda h: (rank[(h["doc"], h["page"])], order.get(h["text"], 0))):
        key = (h["doc"], h["page"])
        if key not in out:
            out[key] = {"doc": h["doc"], "title": titles.get(h["doc"], h["doc"]), "page": h["page"],
                        "score": h["score"], "lines": []}
        out[key]["lines"] += [ln for ln in strip_marks(h["text"]).split("\n")]
    res = []
    for o in out.values():
        lines = []
        for ln in o.pop("lines"):
            if any(ln == x or x.startswith(ln) for x in lines):        # 중복·잘린 앞부분
                continue
            lines = [x for x in lines if not ln.startswith(x)] + [ln]   # 더 긴 문장으로 교체
        o["text"] = "\n".join(lines)
        o["blocks"] = (tables.blocks(o["doc"], o["page"], lines) if tables
                       else [{"type": "text", "text": ln} for ln in lines])   # 원문 표는 표로
        if images:   # 원본 PDF에서 잘라 보여줄 영역(pt): 표 밖 문장 + 표 테두리
            text_lines = [b["text"] for b in o["blocks"] if b["type"] == "text"]
            boxes = [b["bbox"] for b in o["blocks"] if b["type"] == "table"]
            o["region"] = images.region(o["doc"], o["page"], text_lines, boxes)
            pg = images.page(o["doc"], o["page"])
            o["page_width"] = pg["width"] if pg else None
            o["page_height"] = pg["height"] if pg else None
        else:
            o["region"] = None
        res.append(o)
    return sorted(res, key=lambda o: (o["doc"], o["page"]))     # 출처는 쪽 번호 순


class SignWords:
    """답변 문장 → 수어로 표현할 단어(글로스) 목록. 문장에 실제로 있는 말만 뽑으므로 없는 내용이 생기지 않음."""

    def __init__(self, path="data/sign_words.csv"):
        self.rules = []                     # (말, 정확히일치?, 글로스)
        p = Path(path)
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines()[1:]:
                if not ln.strip() or ln.startswith("#") or ln.startswith("말,"):
                    continue
                word, gloss = ln.rsplit(",", 1)
                exact = word.startswith("=")
                self.rules.append((word.lstrip("="), exact, gloss.strip()))

    def __call__(self, sentence):
        out = []
        for ej in re.split(r"[\s,·()（）:]+", sentence):
            core = re.sub(r"^[\d.,~%℃]+", "", ej.strip("“”\"'[]"))      # '4시간' → '시간'
            if not core:
                continue
            found, words = [], []
            for word, exact, gloss in self.rules:
                if (core == word or ej == word) if exact else core.startswith(word):
                    found.append(gloss); words.append(word)
                    break
            # 붙여 쓴 명사(개인보호구, 정전기안전화 등): 3글자 이상 단어는 어절 안 어디에 있어도 찾음
            # (이미 찾은 더 긴 단어의 일부면 제외: '방진마스크' 안의 '마스크')
            for word, exact, gloss in self.rules:
                if (not exact and len(word) >= 3 and word in core[1:] and gloss not in found
                        and not any(word in w for w in words)):
                    found.append(gloss); words.append(word)
            for g in found:
                if g not in out:                                 # 한 문장 안에서 같은 단어는 한 번만
                    out.append(g)
        return out


class SafetySignPipeline:
    def __init__(self, ckpt, index, lexicon=None, motion_dir=None, sentences=None, k=3, demo_threshold=0.45,
                 titles="data/kosha/titles.json", answers="data/kosha/answers.json", min_score=0.11,
                 domain_terms="data/kosha/domain_terms.txt"):
        self._tr, self._tr_args = None, (ckpt, lexicon)       # 번역 모델은 처음 필요할 때 불러옴 (서버 시작 빠르게)
        self.rag = SafetyRAG.load(index)
        self.motion = MotionDict(motion_dir) if motion_dir and Path(motion_dir).exists() else None
        self.k = k
        self.sign_words = SignWords(Path(sentences).parent / "sign_words.csv" if sentences else "data/sign_words.csv")
        # 답변 거절 기준: 문서 검색 1위 점수가 이보다 낮으면 '관련 내용 없음' (환각 방지, docs/환각방지.md)
        self.min_score = min_score
        # 문장 번역 거절 기준: 산업안전 핵심어가 하나도 없으면 번역하지 않음 (번역 모델이 아무 문장이나 안전 수어로 바꾸는 것 방지)
        dt = Path(domain_terms) if domain_terms else None
        self.domain_terms = [w.strip() for w in dt.read_text(encoding="utf-8").splitlines()
                             if w.strip() and not w.startswith("#")] if dt and dt.exists() else []
        # 안내 문장별 문서 근거 답변 (수어 문장은 짧은 안내, 화면 답변은 문서 내용)
        self.answers = json.loads(Path(answers).read_text(encoding="utf-8")) if answers and Path(answers).exists() else {}
        from .pageimage import PageImages
        from .tables import PageTables
        self.tables = PageTables(Path(titles).parent if titles else "data/kosha")
        self.images = PageImages(Path(titles).parent if titles else "data/kosha")
        self._order = {c["text"]: i for i, c in enumerate(self.rag.chunks)}
        self.titles = json.loads(Path(titles).read_text(encoding="utf-8")) if titles and Path(titles).exists() else {}
        self.demo = []
        if sentences and Path(sentences).exists():
            self.demo = json.loads(Path(sentences).read_text(encoding="utf-8"))
        self._memory = {_norm(d["korean"]): d for d in self.demo}
        self.demo_threshold = demo_threshold
        if self.demo:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._dv = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3)).fit([d["korean"] for d in self.demo])
            self._dm = self._dv.transform([d["korean"] for d in self.demo])

    def motion_for(self, glosses):
        """글로스 리스트 → 이어붙인 동작. 동작사전이 없으면 None."""
        return compose(glosses, self.motion) if self.motion else None

    def translate_one(self, text, beam=4):
        hit = self._memory.get(_norm(text))
        if not hit:
            r = self.tr.translate(text, beam=beam)
            r["method"] = "model"
            return r
        tl, t = [], 0.0
        for g in hit["gloss"]:
            clip = self.motion.get(g) if self.motion else None
            d = round(len(clip["frames"]) / clip.get("fps", 30), 3) if clip else 0.8
            tl.append({"gloss": g, "start": round(t, 3), "duration": d})
            t += d
        return {"korean": text, "gloss": hit["gloss"], "timeline": tl, "lexicon_terms": [],
                "dropped_terms": [], "unverified_terms": [], "has_unk": False,
                "method": "memory",                              # 검수된 확정 글로스 사용
                "complete": hit.get("complete", True)}           # False: 일부 단어 동작만 확보

    def sign_sentence(self, text, skip=()):
        """답변 문장 하나 → 그 문장에서 뽑은 수어 단어 + 재생 시간."""
        glosses = [g for g in self.sign_words(text) if g not in skip]
        tl, t = [], 0.0
        for g in glosses:
            clip = self.motion.get(g) if self.motion else None
            d = round(len(clip["frames"]) / clip.get("fps", 30), 3) if clip else 0.0
            tl.append({"gloss": g, "start": round(t, 3), "duration": d})
            t += d
        return {"korean": text, "gloss": glosses, "timeline": tl, "lexicon_terms": [], "dropped_terms": [],
                "unverified_terms": [], "has_unk": False, "method": "extract",   # 답변 문장에서 단어 추출
                "complete": all(self.motion and self.motion.get(g) for g in glosses)}

    def translate_text(self, text, beam=4):
        sents = [self.translate_one(s, beam) for s in split_sentences(text) or [text]]
        return {"sentences": sents, "motion": self.motion_for([g for s in sents for g in s["gloss"]])}

    def best_demo(self, question):
        if not self.demo:
            return None, 0.0
        sc = (self._dm @ self._dv.transform([question]).T).toarray().ravel()
        i = int(np.argmax(sc))
        return self.demo[i], float(sc[i])

    _Q = re.compile(r"(\?|까|나요|니까|어떻게|언제|어디|무엇|뭐|뭘|왜|누가|알려|궁금|어떤|되나|하나|해요\??$|돼요\??$)")

    NO_ANSWER = "문서에서 관련 내용을 찾지 못했습니다."

    def _abstain(self, text, score):
        """근거가 약하면 답하지 않음: 답변·수어·출처 모두 비움."""
        return {"mode": "none", "question": text, "answer_mode": "none", "answer_text": "",
                "sign_text": "", "sentences": [], "motion": None, "sources": [], "top_score": round(score, 4)}

    def query(self, text, beam=4):
        r = self._query(text, beam)
        r["topic"] = self.topic(text) if r.get("mode") != "none" else None
        return r

    def _query(self, text, beam=4):
        """검색창 하나로: 확정 문장이면 그대로 번역, 질문처럼 보이면 문서 검색 후 답변, 아니면 문장 번역.
        안전 문서와 관련이 없는 입력(인사·잡담·문서 밖 주제)은 답하지 않음."""
        if _norm(text) in self._memory:
            return {"mode": "translate", **self.translate_text(text, beam)}
        t = text.strip()
        is_sentence = re.search(r"(다|요|세요|시오)[.!]?$", t) is not None   # 완결된 안내 문장
        if self._Q.search(t) or not is_sentence:                              # 질문 또는 '감전 위험' 같은 키워드
            r = self.ask(text, beam)
            return r if r.get("mode") == "none" else {"mode": "ask", **r}
        if not self.in_domain(t):
            return self._abstain(text, 0.0)
        return {"mode": "translate", **self.translate_text(text, beam)}

    @property
    def tr(self):
        if self._tr is None:
            self._tr = Translator(*self._tr_args)
        return self._tr

    def topic(self, text):
        """질문의 '주제' 키: 같은 안내 문장 또는 같은 문서 쪽으로 답이 가면 같은 주제 (예시 질문 중복 제거용)."""
        hit = self._memory.get(_norm(text))
        if hit:
            return "demo:" + hit["korean"]
        demo, score = self.best_demo(text)
        if self._demo_ok(text, demo, score):
            return "demo:" + demo["korean"]
        h = self.rag.search(text, 1)
        if h and h[0]["score"] >= self.min_score:
            return f"chunk:{self._order.get(h[0]['text'], h[0]['page'])}"   # 같은 문단으로 답이 가면 같은 주제
        return None

    def in_domain(self, text):
        """산업안전 핵심어가 하나라도 들어 있는지 (핵심어 목록이 없으면 항상 True)."""
        t = re.sub(r"\s", "", text)
        return not self.domain_terms or any(w in t for w in self.domain_terms)

    def _clean_answer(self, texts, hits):
        """추출한 답변 문장 정리: 표 칸을 이어붙인 줄(뒤죽박죽)은 빼고, 거의 같은 문장은 하나만."""
        def in_table(t):                          # 그 문장이 나온 쪽의 표에 속하는지 (다른 쪽의 표와는 비교 안 함)
            n = re.sub(r"\s", "", t)
            src = [h for h in hits if n[:20] in re.sub(r"\s", "", strip_marks(h["text"]))] or hits[:1]
            h = src[0]
            return any(b["type"] == "table" for b in self.tables.blocks(h["doc"], h["page"], [t]))

        def similar(a, b):
            A = {a[i:i + 2] for i in range(len(a) - 1)}
            B = {b[i:i + 2] for i in range(len(b) - 1)}
            return len(A & B) / max(1, min(len(A), len(B)))

        plain = [t for t in texts if not in_table(t) and len(t) >= 25] or texts   # 표 줄만 있으면 그대로 둠
        out = []
        for t in plain:
            if not any(similar(re.sub(r"\s", "", t), re.sub(r"\s", "", o)) >= 0.7 for o in out):
                out.append(t)
        return out

    def _demo_ok(self, question, demo, score):
        """준비된 안내 문장으로 답해도 되는지: 유사도 기준 + 핵심어가 질문에 있어야 함."""
        if not demo or score < self.demo_threshold:
            return False
        kws = self.answers.get(demo["korean"], {}).get("keywords")
        return not kws or any(k in re.sub(r"\s", "", question) for k in kws)

    def ask(self, question, beam=4):
        hits = self.rag.search(question, self.k)
        demo, score = self.best_demo(question)
        if self._demo_ok(question, demo, score):
            # 수어: 조원이 확정한 안내 문장 / 화면 답변: 그 문장의 문서 근거 답변
            texts, mode = [demo["korean"]], "demo"
            a = self.answers.get(demo["korean"])
            if a:
                answer_text = a["answer"]
                on_page = [h for h in self.rag.search(question, 60) if h["page"] in a["pages"]]
                hits = (on_page or hits)[:self.k]
            else:
                found, _ = self.rag.answer(question, hits)
                answer_text = " ".join(" ".join(strip_marks(t).split("\n")) for t in found)
        else:
            top = hits[0]["score"] if hits else 0.0
            if top < self.min_score:                       # 근거 약함 → 답하지 않음
                return self._abstain(question, top)
            texts, mode = self.rag.answer(question, hits)
            texts = self._clean_answer([" ".join(strip_marks(t).split("\n")) for t in texts], hits)
            if not texts:
                return self._abstain(question, top)
            answer_text = " ".join(texts)
        # 수어: 답변 문장에 실제로 있는 말만 수어 단어로 (data/sign_words.csv). 번역 모델은 아직 엉뚱한 단어를 만들어서 답변에는 쓰지 않음
        sents, seen = [], set()
        for p in (split_sentences(answer_text) or [answer_text]):
            sg = self.sign_sentence(p, skip=seen)                    # 앞 문장에 나온 단어는 다시 넣지 않음
            seen.update(sg["gloss"])
            if sg["gloss"]:
                sents.append(sg)
        return {
            "question": question,
            "answer_mode": mode,                       # demo | llm | extractive
            "answer_text": answer_text,                # 화면에 보여줄 답변 (문서 근거)
            "sign_text": answer_text,                  # 수어 단어를 뽑은 문장 (= 답변)
            "sentences": sents,                        # 문장별 글로스 + 타임라인
            "motion": self.motion_for([g for s in sents for g in s["gloss"]]),   # 전체 문장 이어붙인 동작
            "sources": merge_sources(hits, self.titles, self._order, self.tables, self.images),   # 출처: 문서 제목·쪽·전체 문장
        }
