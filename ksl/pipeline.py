"""전체 흐름: 질문 → KOSHA 검색 → 쉬운 문장 → 수어 글로스 → (동작사전) → 아바타용 동작.

번역 메모리(demo_sentences.json)
  조원이 글로스·키포인트를 확정한 안내 문장들. 입력 문장이 여기 있으면 모델 대신 확정 글로스를 쓴다.
  (예제 기반 번역 / 번역 메모리 방식 — 자주 쓰는 안전 문구는 검수된 결과를 그대로 쓰는 게 안전)
  질문 모드에서는 질문과 가장 비슷한 안내 문장을 골라 답한다(유사도가 기준 이상일 때).
"""
import json
import os
import re
from pathlib import Path

import numpy as np

from .motion import MotionDict, compose
from .order import arrange
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

    def __init__(self, path="data/sign/sign_words.csv"):
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
        return [g for g, _ in self.with_words(sentence)]

    def with_words(self, sentence):
        """[(글로스, 화면에 보일 말)] — 수어 어순."""
        return [(u["gloss"], u["word"]) for u in self.analyze(sentence)]

    # ---- 형태소 분석으로 답변의 내용어(명사·동사·형용사)를 모두 뽑기 (kiwipiepy) ----
    _kiwi = None
    KEEP = {"NNG", "NNP", "VV", "VA", "VV-I", "VA-I", "VV-R", "VA-R", "XR"}
    STOP_N = {"것", "수", "등", "경우", "때문", "정도", "데", "바", "여부", "사항", "예", "이", "그", "및", "또는", "각", "해당",
              "이하", "이내", "다음", "가지", "자체"}
    STOP_V = {"하다", "되다", "이다", "아니다", "싶다", "보다", "주다", "가지다", "따르다", "위하다", "들다", "대하다", "통하다", "관하다"}
    ADV = {"반드시", "꼭", "절대로", "매일", "평소", "항상", "즉시", "늘", "바로", "절대", "미리", "충분히", "자주", "수시로", "완전히", "정기적으로"}

    @classmethod
    def kiwi(cls):
        if cls._kiwi is None:
            try:
                from kiwipiepy import Kiwi
                cls._kiwi = Kiwi()
                # 보호구 이름 같은 전문 용어는 한 단어로 (정화통 → 정화+통 으로 쪼개지지 않게)
                for w in ("정화통", "방진마스크", "방독마스크", "송기마스크", "보안경", "보안면", "안전대", "안전화", "안전모",
                          "귀마개", "귀덮개", "핏테스트", "정전기", "절연용", "보호구", "개인보호구", "청력보호구", "호흡보호구",
                          "낙하물", "사업주", "세정제", "여과재", "방열복", "보호복", "보호장갑", "안전보건표지"):
                    cls._kiwi.add_user_word(w, "NNG", 0)
            except Exception:                       # 설치 안 됨 → 단어표 방식으로
                cls._kiwi = False
        return cls._kiwi

    def _gloss_for(self, lemma, eojeol):
        """뽑은 말 → 수어 단어(글로스). 단어표(sign_words.csv)에 있으면 그 글로스(동작 있는 단어로 연결), 없으면 말 그대로."""
        for word, exact, gloss in self.rules:
            if exact and eojeol == word:
                return gloss
        for word, exact, gloss in self.rules:
            if not exact and (lemma == word or lemma.startswith(word)):
                return gloss
        return lemma

    def analyze(self, sentence):
        """답변 문장 → 수어 단어 [{gloss, word(화면에 보일 말), nms(문법 표정·고개)}], 한국수어 어순 (ksl/order.py)."""
        kw = self.kiwi()
        if not kw:
            return [{"gloss": g, "word": w, "nms": None} for g, w in self._with_words_rules(sentence)]
        # 어절 위치 (화면에는 어절 모양 그대로 보여줌)
        spans = [(m.start(), m.end()) for m in re.finditer(r"[^\s,·()（）:]+", sentence)]
        def eoj(i):
            for k, (a, b) in enumerate(spans):
                if a <= i < b:
                    return k
            return None
        toks = kw.tokenize(sentence)
        items = []                                    # (글로스, 어절번호, 시작위치, 말, 토큰번호)
        i = 0
        while i < len(toks):
            t, ti = toks[i], i
            k = eoj(t.start)
            lemma, start = None, t.start
            if t.tag == "SN" and i + 1 < len(toks) and toks[i + 1].tag in ("NNB", "NNG") and eoj(toks[i + 1].start) == k:
                lemma = t.form + toks[i + 1].form; i += 1                  # 6 + 개월 → 6개월
            elif t.tag == "XPN" and i + 1 < len(toks) and toks[i + 1].tag in ("NNG", "NNP"):
                lemma = t.form + toks[i + 1].form; i += 1                  # 비 + 마모
            elif t.tag in self.KEEP:
                lemma = t.form + ("다" if t.tag.startswith(("VV", "VA")) else "")
            elif t.tag == "MAG" and t.form in self.ADV:
                lemma = t.form
            if lemma and i + 1 < len(toks) and toks[i + 1].tag == "XSN" and eoj(toks[i + 1].start) == k:
                lemma += toks[i + 1].form; i += 1                          # 제조 + 자 → 제조자
            i += 1
            if not lemma or k is None or lemma in self.STOP_N or lemma in self.STOP_V:
                continue
            if lemma in ("있다", "없다") and any(x.form == "수" and x.tag == "NNB" for x in toks[max(0, i - 3):i]):
                continue                                                   # '할 수 있다/없다' 는 가능·못하다로 (ksl/order.py)
            a, b = spans[k]
            ej = sentence[a:b].strip("“”\"'[]").rstrip(".!?。")
            items.append((self._gloss_for(lemma, ej), k, start, lemma, ti))
        units = []
        for n, (g, k, start, lemma, ti) in enumerate(items):
            a, b = spans[k]
            same = [x for x in items if x[1] == k]
            last = same[-1][2] == start
            nxt = next((x[2] for x in items[n + 1:] if x[1] == k), b)
            # 어절에 단어가 하나면 어절 그대로('세척한'), 여러 개면 앞 단어는 그 부분만, 마지막은 어절 끝까지
            # (단어 앞에 붙은 조사·기호는 빼고: '(RH<70%)를유지해야' → '유지해야')
            shown = sentence[start:b] if last else sentence[start:nxt]
            shown = shown.strip("“”\"'[]").rstrip(".!?。")
            if shown:                                    # 같은 단어가 다시 나와도 그대로 (수어도 반복함)
                units.append({"gloss": g, "word": shown, "lemma": lemma, "ti": ti})
        return arrange(units, toks)

    def _with_words_rules(self, sentence):
        """[(글로스, 답변에 쓰인 모양)] — 화면의 단어 블록은 답변과 같은 모양으로 보여줌
        (예: 이상하다1 → '이상이', 깨끗하다 → '깨끗하게'). 수어 동작은 글로스 그대로."""
        out, seen = [], set()
        for ej in re.split(r"[\s,·()（）:]+", sentence):
            disp = ej.strip("“”\"'[]").rstrip(".!?。")                       # 화면에 보일 모양 ('6개월마다')
            core = re.sub(r"^[\d.,~%℃]+", "", disp)                       # 단어 찾기용 ('4시간' → '시간')
            pre = disp[:len(disp) - len(core)]
            if not core:
                continue
            found = []                                           # (글로스, 말, 어절 안 위치)
            for word, exact, gloss in self.rules:
                if (core == word or ej == word) if exact else core.startswith(word):
                    found.append((gloss, word, 0))
                    break
            # 붙여 쓴 명사(개인보호구, 정전기안전화 등): 3글자 이상 단어는 어절 안 어디에 있어도 찾음
            # (이미 찾은 더 긴 단어의 일부면 제외: '방진마스크' 안의 '마스크')
            for word, exact, gloss in self.rules:
                if (not exact and len(word) >= 3 and word in core[1:] and gloss not in [f[0] for f in found]
                        and not any(word in f[1] for f in found)):
                    found.append((gloss, word, core.index(word, 1)))
            found.sort(key=lambda f: f[2])
            for k, (g, word, pos) in enumerate(found):
                # 어절에 단어가 하나면 어절 그대로('이상이'), 여러 개면 앞 단어는 그 말만, 마지막은 뒤 조사까지('개인'+'보호구는')
                shown = disp if len(found) == 1 else (core[pos:] if k == len(found) - 1 else (pre if pos == 0 else "") + word)
                if g not in seen:                                # 한 문장 안에서 같은 단어는 한 번만
                    seen.add(g)
                    out.append((g, shown))
        return out


class SafetySignPipeline:
    def __init__(self, ckpt, index, lexicon=None, motion_dir=None, sentences=None, k=3, demo_threshold=0.45,
                 titles="data/kosha/titles.json", answers="data/kosha/answers.json", min_score=0.11,
                 domain_terms="data/kosha/domain_terms.txt"):
        self._tr, self._tr_args = None, (ckpt, lexicon)       # 번역 모델은 처음 필요할 때 불러옴 (서버 시작 빠르게)
        self.rag = SafetyRAG.load(index)
        self.motion = MotionDict(motion_dir) if motion_dir and Path(motion_dir).exists() else None
        from .missing import MissingLog
        self.missing = MissingLog()                 # 동작이 없는 단어 기록 (GET /missing)
        from .fingerspell import load_table
        self.fingerspell = load_table(Path(motion_dir).parent / "fingerspell.json" if motion_dir else "data/sign/fingerspell.json") \
            if os.environ.get("KSL_FINGERSPELL", "0") == "1" else None   # 동작이 없는 단어 → 지문자 (기본 끔, KSL_FINGERSPELL=1 이면 켬 — 손 모양 검수 전)
        self.k = k
        self.sign_words = SignWords(Path(sentences).parent / "sign_words.csv" if sentences else "data/sign/sign_words.csv")
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

    def motion_for(self, glosses, nms=None, texts=None, log=True):
        """글로스 리스트 → 이어붙인 동작. 동작사전이 없으면 None.
        nms: 글로스마다 문법 표정·고개 (ksl/order.py) → segment 의 'nms' 로 (아바타가 그 단어 동안 표정·고개).
        texts: 글로스마다 나온 문장 → 동작이 없는 단어 기록(ksl/missing.py)의 예시로. log=False 면 기록 안 함 (단어 다시 보기 등)."""
        if not self.motion:
            return None
        m = compose(glosses, self.motion, fingerspell=self.fingerspell, fs_words=self._fs_pick(glosses) if log else None)
        for seg, n in zip(m["segments"], nms or []):
            seg["nms"] = n
        miss = [(i, s["gloss"]) for i, s in enumerate(m["segments"]) if not s["has_motion"] or s.get("fingerspell")]
        if log and miss and self.missing is not None:
            self.missing.record([g for _, g in miss], [texts[i] if texts and i < len(texts) else None for i, _ in miss])
        return m

    def _fs_pick(self, glosses, limit=None):
        """답변에서 지문자로 쓸 단어 고르기: 전부 지문자로 하면 재생이 너무 길어짐 (단어 하나에 2~4초)
        → 검토 중 용어(FS:) 먼저, 그다음 산업안전 핵심어(data/kosha/domain_terms.txt)가 든 단어, 최대 limit 개 (KSL_FS_LIMIT, 기본 3).
        나머지는 회색 (단어를 누르면 그 단어만 지문자로 볼 수 있음)."""
        from .fingerspell import spell_text
        limit = int(os.environ.get("KSL_FS_LIMIT", 3)) if limit is None else limit
        cand = [g for g in dict.fromkeys(glosses) if not (self.motion and self.motion.get(g))]
        rank = lambda g: 0 if g.startswith("FS:") else 1 if any(t in spell_text(g) for t in self.domain_terms) else 2
        return set(sorted(cand, key=lambda g: (rank(g), cand.index(g)))[:limit])

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
        units = [u for u in self.sign_words.analyze(text) if u["gloss"] not in skip]   # 한국수어 어순 (ksl/order.py)
        glosses = [u["gloss"] for u in units]
        tl, t = [], 0.0
        for u in units:
            clip = self.motion.get(u["gloss"]) if self.motion else None
            d = round(len(clip["frames"]) / clip.get("fps", 30), 3) if clip else 0.0
            tl.append({"gloss": u["gloss"], "start": round(t, 3), "duration": d, "nms": u["nms"]})
            t += d
        return {"korean": text, "gloss": glosses, "words": [u["word"] for u in units], "nms": [u["nms"] for u in units],
                "timeline": tl, "lexicon_terms": [], "dropped_terms": [],
                "unverified_terms": [], "has_unk": False, "method": "extract",   # 답변 문장에서 단어 추출
                "complete": all(self.motion and self.motion.get(g) for g in glosses)}

    def translate_text(self, text, beam=4):
        sents = [self.translate_one(s, beam) for s in split_sentences(text) or [text]]
        return {"sentences": sents, "motion": self.motion_for([g for s in sents for g in s["gloss"]],
                                                              texts=[s["korean"] for s in sents for _ in s["gloss"]])}

    def best_demo(self, question):
        if not self.demo:
            return None, 0.0
        sc = (self._dm @ self._dv.transform([question]).T).toarray().ravel()
        i = int(np.argmax(sc))
        return self.demo[i], float(sc[i])

    _Q = re.compile(r"(\?|까|나요|니까|어떻게|언제|어디|무엇|뭐|뭘|왜|누가|알려|궁금|어떤|되나|하나|해요\??$|돼요\??$)")

    NO_ANSWER = "개인보호구의 사용 및 관리에 관한 기술지원규정에서 관련 내용을 찾지 못했습니다."

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
        if h and self._evidence(self.rag.search(text, self.k)) >= self.min_score:
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

    @staticmethod
    def _evidence(hits):
        """문서 근거의 세기: 검색 상위 k 개 중 글자 TF-IDF 점수가 가장 높은 것.
        (단어 BM25 가 1위를 바꿔도, 상위 안에 질문과 글자가 많이 겹치는 문단이 있으면 근거 있음)"""
        return max((h["score"] for h in hits), default=0.0)

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
            top = self._evidence(hits)
            if top < self.min_score:                       # 근거 약함 → 답하지 않음
                return self._abstain(question, top)
            texts, mode = self.rag.answer(question, hits)
            if mode == "llm" and any("찾을 수 없" in t for t in texts):   # Claude 도 문서에서 근거를 못 찾음 → 답하지 않음
                return self._abstain(question, top)
            texts = self._clean_answer([" ".join(strip_marks(t).split("\n")) for t in texts], hits)
            if not texts:
                return self._abstain(question, top)
            answer_text = " ".join(texts)
        # 수어: 답변 문장에 실제로 있는 말만 수어 단어로 (data/sign/sign_words.csv). 번역 모델은 아직 엉뚱한 단어를 만들어서 답변에는 쓰지 않음
        sents = []
        for p in (split_sentences(answer_text) or [answer_text]):
            sg = self.sign_sentence(p)                               # 앞 문장에 나온 단어도 답변에 있으면 그대로
            if sg["gloss"]:
                sents.append(sg)
        return {
            "question": question,
            "answer_mode": mode,                       # demo | llm | extractive
            "answer_text": answer_text,                # 화면에 보여줄 답변 (문서 근거)
            "sign_text": answer_text,                  # 수어 단어를 뽑은 문장 (= 답변)
            "sentences": sents,                        # 문장별 글로스 + 타임라인
            "motion": self.motion_for([g for s in sents for g in s["gloss"]],     # 전체 문장 이어붙인 동작
                                      [n for s in sents for n in s["nms"]],
                                      [s["korean"] for s in sents for _ in s["gloss"]]),
            "sources": merge_sources(hits, self.titles, self._order, self.tables, self.images),   # 출처: 문서 제목·쪽·전체 문장
        }
