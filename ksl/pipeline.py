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


def merge_sources(hits, titles, order=None, tables=None):
    """같은 문서·쪽은 하나로 합치고(겹치는 문장 제거, 문서 순서대로) 번호 표시를 뗀 전체 문장으로."""
    out = {}
    rank = {}
    for i, h in enumerate(hits):                      # 쪽 순서는 검색 점수 순, 쪽 안의 문장은 문서 순
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
        res.append(o)
    return res


class SafetySignPipeline:
    def __init__(self, ckpt, index, lexicon=None, motion_dir=None, sentences=None, k=3, demo_threshold=0.45,
                 titles="data/kosha/titles.json", answers="data/kosha/answers.json"):
        self.tr = Translator(ckpt, lexicon)
        self.rag = SafetyRAG.load(index)
        self.motion = MotionDict(motion_dir) if motion_dir and Path(motion_dir).exists() else None
        self.k = k
        # 안내 문장별 문서 근거 답변 (수어 문장은 짧은 안내, 화면 답변은 문서 내용)
        self.answers = json.loads(Path(answers).read_text(encoding="utf-8")) if answers and Path(answers).exists() else {}
        from .tables import PageTables
        self.tables = PageTables(Path(titles).parent if titles else "data/kosha")
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

    def query(self, text, beam=4):
        """검색창 하나로: 확정 문장이면 그대로 번역, 질문처럼 보이면 문서 검색 후 답변, 아니면 문장 번역."""
        if _norm(text) in self._memory:
            return {"mode": "translate", **self.translate_text(text, beam)}
        t = text.strip()
        is_sentence = re.search(r"(다|요|세요|시오)[.!]?$", t) is not None   # 완결된 안내 문장
        if self._Q.search(t) or not is_sentence:                              # 질문 또는 '감전 위험' 같은 키워드
            return {"mode": "ask", **self.ask(text, beam)}
        return {"mode": "translate", **self.translate_text(text, beam)}

    def ask(self, question, beam=4):
        hits = self.rag.search(question, self.k)
        demo, score = self.best_demo(question)
        kws = self.answers.get(demo["korean"], {}).get("keywords") if demo else None
        on_topic = not kws or any(k in re.sub(r"\s", "", question) for k in kws)   # 핵심어가 있어야 준비된 문장으로 답함
        if demo and score >= self.demo_threshold and on_topic:
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
            texts, mode = self.rag.answer(question, hits)
            texts = [" ".join(strip_marks(t).split("\n")) for t in texts]
            answer_text = " ".join(texts)
        sents = [self.translate_one(p, beam) for t in texts for p in (split_sentences(t) or [t])]
        return {
            "question": question,
            "answer_mode": mode,                       # demo | llm | extractive
            "answer_text": answer_text,                # 화면에 보여줄 답변 (문서 근거)
            "sign_text": " ".join(texts),              # 아바타가 수어로 보여주는 문장
            "sentences": sents,                        # 문장별 글로스 + 타임라인
            "motion": self.motion_for([g for s in sents for g in s["gloss"]]),   # 전체 문장 이어붙인 동작
            "sources": merge_sources(hits, self.titles, self._order, self.tables),   # 출처: 문서 제목·쪽·전체 문장
        }
