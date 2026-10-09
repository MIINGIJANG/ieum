"""KOSHA GUIDE RAG: 문서 → chunk → 검색 → (LLM/추출식) 쉬운 답변 문장.

검색기 2종
- tfidf (기본): 글자 2~4gram TF-IDF. 설치·인터넷 불필요, 한국어 조사 변화에 강함.
- dense (선택): sentence-transformers 임베딩 (예: jhgan/ko-sroberta-multitask).
  `pip install sentence-transformers` 후 --retriever dense.
"""
import json
import os
import pickle
import re
from pathlib import Path

import numpy as np


# ---------------- 문서 적재 / 분할 ----------------
def _clean_page(t):
    """머리말(KOSHA GUIDE, 문서번호)·쪽번호 줄 제거, 겹친 공백 정리."""
    out = []
    for line in t.splitlines():
        x = line.strip()
        if re.fullmatch(r"(KOSHA\s*GUIDE|[A-Z]\s*[–-]\s*[A-Z]\s*[–-]\s*\d+\s*[–-]\s*\d{4}|-\s*\d+\s*-)", x):
            continue
        if re.search(r"[·…\.]{6,}", x):        # 목차의 점선 줄
            continue
        out.append(re.sub(r"[ \t]{2,}", " ", x))
    return "\n".join(out)


def load_pages(path):
    """PDF는 페이지별, txt는 '\f' 또는 '=== page N ===' 기준으로 페이지 분리."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        pages = []
        for i, p in enumerate(PdfReader(str(path)).pages):
            try:      # layout 모드: 한글 PDF에서 띄어쓰기가 사라지는 문제 방지
                t = p.extract_text(extraction_mode="layout") or ""
            except Exception:
                t = p.extract_text() or ""
            pages.append((i + 1, _clean_page(t)))
        return pages
    text = path.read_text(encoding="utf-8")
    marks = list(re.finditer(r"^=+\s*page\s*(\d+)\s*=+\s*$", text, flags=re.M | re.I))
    if marks:   # '=== page N ===' 표식이 있으면 그 번호를 그대로 사용
        ends = [m.start() for m in marks[1:]] + [len(text)]
        return [(int(m.group(1)), text[m.end():e]) for m, e in zip(marks, ends)]
    parts = [p for p in text.split("\f") if p.strip()]
    return [(i + 1, p) for i, p in enumerate(parts)]


def _sentences(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?m)^\s*(?:\d+(?:\.\d+)*[.)]|[가-하][.)])\s*", "\n\n", text)   # '3.' '가)' 번호 = 항목 경계
    sents = re.split(r"(?<=[.!?])\s+|\n(?=\s*[-•○◦□])|\n{2,}", text)
    sents = [re.sub(r"\s+", " ", s).strip(" -•○◦□") for s in sents]
    return [s for s in sents if len(s) > 3]


def chunk_pages(pages, doc_name, max_chars=350, overlap=1):
    """문장 경계를 지키며 max_chars 이하로 묶고, 앞 chunk의 마지막 문장 overlap개를 겹침."""
    chunks = []
    for page, text in pages:
        sents = _sentences(text)
        cur = []
        for s in sents:
            if cur and sum(len(x) for x in cur) + len(s) > max_chars:
                chunks.append({"doc": doc_name, "page": page, "text": "\n".join(cur)})
                cur = cur[-overlap:] if overlap else []
            cur.append(s)
        if cur:
            chunks.append({"doc": doc_name, "page": page, "text": "\n".join(cur)})
    for i, c in enumerate(chunks):
        c["id"] = i
    return chunks


# ---------------- 질문 확장 (현장 말 → 문서 용어) ----------------
# 작업자는 '파편이 튀는', '고장 나면' 처럼 말하지만 문서는 '흩날릴', '이상·수리·교환' 처럼 씀.
# 일상어 → 지침 용어를 질문 뒤에 덧붙여 검색 (문서 쪽은 그대로 둠)
QUERY_SYNONYMS = {
    r"파편|튀[는면어]|튀겨": "흩날릴 흩날리는",
    r"고장|망가|부서|찢어|깨[진지]": "이상 수리 교환",
    r"시끄러|소음|귀": "소음 청력보호구 귀마개",
    r"잠깐|잠시|몇\s*분|짧은": "몇 분 동안 예외",
    r"같이\s*쓰|공용|돌려\s*쓰|여러\s*명": "공동사용 개인 전용",
    r"높은\s*곳|추락|떨어[져지질]": "추락 떨어질 안전대",
    r"먼지|가루": "분진 방진",
    r"뜨거|열기|화상": "고열 방열",
    r"(?<!정)전기|감전|전압": "감전 절연 내전압성",
    r"추운|냉동|영하": "저온 방한",
    r"신발|구두|장화": "안전화",
    r"장갑": "안전장갑",
    r"눈\s|눈을|눈에": "눈 보호구 보안경",
    r"얼굴": "보안면 안면",
    r"산소.{0,3}(부족|없)": "산소결핍",
    r"쓰러|구조|구하": "구출",
    r"더운|폭염|더위": "폭염",
    r"교육|훈련|배워": "교육훈련",
    r"씻|세척|청소|닦": "세척 세정제",
    r"보관|넣어\s*두|두는": "보관 보관함",
    r"필터": "필터 교환 충분한 양",
    r"AE|ABE|AB형": "안전모 종류 내전압성",
    r"마지막|최후": "최후의 수단",
}


def expand_query(q):
    extra = [v for k, v in QUERY_SYNONYMS.items() if re.search(k, q)]
    return q + (" " + " ".join(extra) if extra else "")


# ---------------- 검색기 ----------------
LLM_STATUS = {"last": None}   # 마지막 Claude 호출 결과 (/llm_check 로 확인)


class TfidfRetriever:
    kind = "tfidf"

    def fit(self, texts):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.mat = self.vec.fit_transform(texts)
        return self

    def scores(self, query):
        return (self.mat @ self.vec.transform([query]).T).toarray().ravel()


class DenseRetriever:
    kind = "dense"

    def __init__(self, model_name="jhgan/ko-sroberta-multitask"):
        self.model_name = model_name

    def _model(self):
        if not hasattr(self, "_m"):
            from sentence_transformers import SentenceTransformer
            self._m = SentenceTransformer(self.model_name)
        return self._m

    def fit(self, texts):
        self.emb = self._model().encode(texts, normalize_embeddings=True, show_progress_bar=True)
        return self

    def scores(self, query):
        q = self._model().encode([query], normalize_embeddings=True)[0]
        return self.emb @ q

    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop("_m", None)
        return d


class SafetyRAG:
    def __init__(self, chunks, retriever):
        self.chunks, self.retriever = chunks, retriever

    @classmethod
    def build(cls, files, retriever="tfidf", dense_model=None, max_chars=350):
        chunks = []
        for f in files:
            for c in chunk_pages(load_pages(f), Path(f).stem, max_chars):
                c["id"] = len(chunks)
                chunks.append(c)
        r = TfidfRetriever() if retriever == "tfidf" else DenseRetriever(dense_model or "jhgan/ko-sroberta-multitask")
        r.fit([c["text"] for c in chunks])
        return cls(chunks, r)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"chunks": self.chunks, "retriever": self.retriever}, f)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            d = pickle.load(f)   # 우리 팀이 만든 인덱스 파일만 로드할 것
        return cls(d["chunks"], d["retriever"])

    def search(self, query, k=3, expand=True):
        s = self.retriever.scores(expand_query(query) if expand else query)
        idx = np.argsort(-s)[:k]
        return [{**self.chunks[i], "score": round(float(s[i]), 4)} for i in idx]

    # ---------- 답변 문장 만들기 ----------
    def answer(self, query, hits, max_sentences=2):
        """ANTHROPIC_API_KEY 가 있으면 LLM으로 '근거 안에서만' 쉬운 문장 생성,
        없으면 검색 chunk에서 질문과 가장 비슷한 문장을 그대로 추출(환각 없음)."""
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                out = self._llm_answer(query, hits, max_sentences)
                LLM_STATUS["last"] = "정상"
                return out, "llm"
            except Exception as e:  # LLM 실패 시 추출식으로 대체
                LLM_STATUS["last"] = f"오류: {str(e)[:200]}"
                print(f"[rag] LLM 실패 → 추출식 사용: {e}")
        return self._extractive(query, hits, max_sentences), "extractive"

    def _extractive(self, query, hits, n):
        """검색된 chunk 의 문장(목록 항목 포함) 중 질문과 가장 맞는 문장을 그대로 뽑음.
        점수 = 문장-질문 유사도 + 상위 chunk 가산점 (검색 순위를 존중)."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        cand, seen = [], set()
        for rank, h in enumerate(hits):
            for s in h["text"].split("\n"):
                s = s.strip()
                code = len(re.findall(r"[A-Za-z0-9\-]", s)) / max(len(s.replace(" ", "")), 1)
                if len(s) >= 20 and s not in seen and code < 0.2:   # 짧은 제목줄, 기호만 나열된 표 줄 제외
                    seen.add(s)
                    cand.append((s, rank))
        if not cand:
            return []
        q = expand_query(query)
        sents = [c[0] for c in cand]
        v = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit(sents + [q])
        sc = (v.transform(sents) @ v.transform([q]).T).toarray().ravel()
        sc = sc + np.array([0.08 * (len(hits) - r) / len(hits) for _, r in cand])
        top = np.argsort(-sc)[:n]
        top = [i for i in top if sc[i] >= 0.75 * sc[top[0]]]  # 1위보다 많이 낮은 문장은 버림
        return [sents[i] for i in top]

    def _llm_answer(self, query, hits, n):
        import httpx
        ctx = "\n".join(f"[{h['doc']} p.{h['page']}] {h['text']}" for h in hits)
        prompt = (
            "아래 산업안전 문서 내용만 근거로 질문에 답하세요. 문서에 없는 내용은 만들지 마세요.\n"
            f"- 수어로 번역할 것이므로 짧고 쉬운 평서문 {n}문장 이내, 한 문장에 한 가지 내용\n"
            "- 각 문장은 줄바꿈으로 구분, 번호·기호 없이\n"
            "- 근거가 없으면 '문서에서 찾을 수 없습니다' 한 줄만\n\n"
            f"[문서]\n{ctx}\n\n[질문]\n{query}"
        )
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
                  "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"])
        return [s.strip() for s in text.split("\n") if s.strip()][:n]


def load_questions(path):
    """평가 질문 CSV: question,gold_page,gold_keyword (gold_* 중 하나 이상)."""
    import csv
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_hit(chunk, q):
    page = (q.get("gold_page") or "").strip()
    kw = (q.get("gold_keyword") or "").strip()
    ok_page = (not page) or str(chunk["page"]) == page
    ok_kw = (not kw) or all(k.strip() in chunk["text"] for k in kw.split("|"))
    return ok_page and ok_kw and (page or kw)


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)
