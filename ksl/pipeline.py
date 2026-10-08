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


class SafetySignPipeline:
    def __init__(self, ckpt, index, lexicon=None, motion_dir=None, sentences=None, k=3, demo_threshold=0.45):
        self.tr = Translator(ckpt, lexicon)
        self.rag = SafetyRAG.load(index)
        self.motion = MotionDict(motion_dir) if motion_dir and Path(motion_dir).exists() else None
        self.k = k
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
        if demo and score >= self.demo_threshold:
            texts, mode = [demo["korean"]], "demo"           # 준비된 안내 문장으로 답변
        else:
            texts, mode = self.rag.answer(question, hits)
        sents = [self.translate_one(p, beam) for t in texts for p in (split_sentences(t) or [t])]
        return {
            "question": question,
            "answer_mode": mode,                       # demo | llm | extractive
            "answer_text": " ".join(texts),
            "sentences": sents,                        # 문장별 글로스 + 타임라인
            "motion": self.motion_for([g for s in sents for g in s["gloss"]]),   # 전체 문장 이어붙인 동작
            "sources": [{"doc": h["doc"], "page": h["page"], "score": h["score"],
                         "text": h["text"]} for h in hits],   # 출처 표시용
        }
