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


_HEAD = re.compile(r"^\s*(\d{1,2})\.((?:\d{1,2}\.?){0,2})\s*(.*)$")


def _heading(line, top):
    """'7.2안전화의 종류' 같은 절 제목이면 (큰 번호, 제목), 아니면 None.
    표 안의 '1.용광로, 평로…' 같은 번호 목록과 구분: 큰 절은 번호가 차례대로(top+1), 작은 절은 지금 큰 절 번호로 시작,
    제목은 짧고 쉼표가 없어야 함."""
    m = _HEAD.match(line)
    if not m:
        return None
    major, sub = int(m.group(1)), m.group(2).rstrip(".")
    title = re.sub(r"\s+", " ", m.group(3).split("(")[0]).strip()
    if not re.match(r"[가-힣]", title) or len(title) > 24 or "," in title:
        return None
    if sub:
        return (major, f"{major}.{sub} {title}") if major == top else None
    if re.search(r"장소|업무|하는|하거나", title):        # '7.녹인 금속을 운반하거나 주입하는 장소' (표 안 번호 목록)
        return None
    return (major, f"{major}. {title}") if major == top + 1 else None


def _page_sections(pages):
    """쪽마다 [((큰 절, 작은 절), 그 절의 글)] — 절이 쪽을 넘어가도 이어짐."""
    top, big, small, out = 0, "", "", []
    for page, text in pages:
        segs, buf = [], []
        for line in text.split("\n"):
            h = _heading(line, top)
            if h:
                segs.append(((big, small), "\n".join(buf)))
                buf = []
                if h[1].startswith(f"{h[0]}. "):
                    top, big, small = h[0], h[1], ""
                else:
                    small = h[1]
            buf.append(line)
        segs.append(((big, small), "\n".join(buf)))
        out.append((page, [(sec, t) for sec, t in segs if t.strip()]))
    return out


def chunk_pages(pages, doc_name, max_chars=350, overlap=1):
    """문장 경계를 지키며 max_chars 이하로 묶고, 앞 chunk의 마지막 문장 overlap개를 겹침.
    chunk 마다 속한 절 제목(section)도 기록 → 검색 때 '7.2 안전화의 종류' 같은 문맥으로 씀 (표 칸만 있는 chunk 도 찾게)."""
    chunks = []
    for page, segs in _page_sections(pages):
        sents = [(s, sec) for sec, t in segs for s in _sentences(t)]
        cur = []

        def flush():
            secs = list(dict.fromkeys(x for _, sec in cur for x in sec if x))   # 큰 절·작은 절 제목 (겹치지 않게)
            chunks.append({"doc": doc_name, "page": page, "text": "\n".join(s for s, _ in cur), "section": " ".join(secs)})
        for s in sents:
            if cur and sum(len(x) for x, _ in cur) + len(s[0]) > max_chars:
                flush()
                cur = cur[-overlap:] if overlap else []
            cur.append(s)
        if cur:
            flush()
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
    r"용광로|제련|쇳물|녹인\s*금속|용해로": "고열작업 방열복 방열장갑",
    r"(?<!정)전기|감전|전압": "감전 절연 내전압성",
    r"추운|냉동|영하|차가|차갑": "저온 저온물체 방한",
    r"신발|구두|장화|작업화|운동화": "안전화",
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
    r"오토바이|헬멧|이륜": "이륜자동차 승차용 안전모",
    r"여러\s*(개|가지)|겹쳐|(같이|함께)\s*(착용|입|끼)": "한 종류 이상 착용 조화",
    r"누가\s*(확인|점검|검사)|착용.{0,8}(확인|점검|검사)": "사용되고 있는지 정기적으로 검사",
}


def expand_query(q):
    extra = [v for k, v in QUERY_SYNONYMS.items() if re.search(k, q)]
    return q + (" " + " ".join(extra) if extra else "")


# ---------------- 검색기 ----------------
# 단어 BM25(절 제목 포함)를 글자 TF-IDF 에 더하는 비율, 같은 쪽 앞·뒤 chunk 점수를 더하는 비율.
# 개발용·heldout·heldout2 에서 1.0~2.0 / 0.1~0.15 가 같은 성능이라 그 가운데 (heldout3 은 비교용, docs/retrieval.md)
HYBRID_W = 1.2
NEIGHBOR_W = 0.15
_KIWI = None


def _words(text):
    """형태소 분석으로 명사·동사·형용사 어간 등 내용어만 (조사·어미 제외)."""
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi
        _KIWI = Kiwi()
    return [t.form for t in _KIWI.tokenize(text) if t.tag.startswith(("NN", "VV", "VA", "SL", "SN", "XR"))]


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75)."""

    def __init__(self, docs, k1=1.5, b=0.75):
        from collections import Counter
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = self.len.mean() or 1.0
        n, df = len(docs), Counter(w for d in docs for w in set(d))
        self.idf = {w: np.log((n - c + 0.5) / (c + 0.5) + 1) for w, c in df.items()}

    def scores(self, q):
        out = np.zeros(len(self.tf))
        norm = self.k1 * (1 - self.b + self.b * self.len / self.avg)
        for w in q:
            if w not in self.idf:        # 문서에 없는 합성어는 앞부분으로 ('잠수부' → '잠수', '작업화' → '작업')
                w = next((w[:n] for n in range(len(w) - 1, 1, -1) if w[:n] in self.idf), None)
                if w is None:
                    continue
            f = np.array([tf.get(w, 0) for tf in self.tf], dtype=float)
            out += self.idf[w] * f * (self.k1 + 1) / (f + norm)
        return out


LLM_STATUS = {"last": None, "ok": 0, "fail": 0, "last_error": None, "last_error_at": None}  # Claude 호출 결과 (/llm_check 로 확인)
_LLM_CACHE = {}               # (질문, 검색 결과) → Claude 답변
_CLIENT = None                # Anthropic SDK 클라이언트 (연결 재사용)


def client():
    """Anthropic SDK 클라이언트 (연결 재사용, 답변 만들기·수어 문장 만들기가 함께 씀)."""
    global _CLIENT
    if _CLIENT is None:
        import anthropic
        # 붙여 넣을 때 섞인 공백·줄바꿈·따옴표는 떼고 씀 (그대로 보내면 인증 실패)
        key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip().strip("\"'").strip()
        _CLIENT = anthropic.Anthropic(api_key=key or None, timeout=30.0, max_retries=2)
    return _CLIENT


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


INDEX_VERSION = 2     # 2: chunk 마다 절 제목(section)


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
            pickle.dump({"version": INDEX_VERSION, "chunks": self.chunks, "retriever": self.retriever}, f)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            d = pickle.load(f)   # 우리 팀이 만든 인덱스 파일만 로드할 것
        if d.get("version") != INDEX_VERSION:     # 예전 인덱스(절 제목 없음) → 서버는 새로 만듦 (api.py _ensure_index)
            raise ValueError(f"예전 형식의 인덱스입니다: {path} — scripts/data/build_index.py 로 다시 만드세요")
        return cls(d["chunks"], d["retriever"])

    def search(self, query, k=3, expand=True):
        """순위 = 글자 TF-IDF + HYBRID_W × 단어 BM25(절 제목 포함) (둘 다 1위=1 로 맞춤)
                 + NEIGHBOR_W × 같은 쪽 바로 앞·뒤 chunk 의 순위 점수 중 큰 것.
        'score' 는 그 chunk 의 TF-IDF 점수 (답변 거절 기준 min_score 는 상위 k 개 중 가장 높은 score 기준)."""
        q = expand_query(query) if expand else query
        s = self.retriever.scores(q)
        rank = s / (s.max() or 1)
        bm = self._bm25()
        if bm is not None and HYBRID_W:
            b = bm.scores(_words(q))
            rank = rank + HYBRID_W * b / (b.max() or 1)
        if NEIGHBOR_W:
            rank = rank + NEIGHBOR_W * self._neighbor_max(rank)
        idx = np.argsort(-rank)[:k]
        return [{**self.chunks[i], "score": round(float(s[i]), 4)} for i in idx]

    def _neighbor_max(self, rank):
        """표가 여러 chunk 로 잘리면 '용광로'(작업 칸)와 '방열복'(보호구 칸)이 다른 chunk 에 들어감
        → 같은 쪽 바로 옆 chunk 가 질문과 맞으면 이 chunk 도 조금 올려 줌."""
        out = np.zeros_like(rank)
        for i, c in enumerate(self.chunks):
            nb = [rank[j] for j in (i - 1, i + 1)
                  if 0 <= j < len(self.chunks) and self.chunks[j]["page"] == c["page"] and self.chunks[j]["doc"] == c["doc"]]
            out[i] = max(nb, default=0.0)
        return out

    def _bm25(self):
        if not hasattr(self, "_bm"):
            try:
                self._bm = BM25([_words(c.get("section", "") + " " + c["text"]) for c in self.chunks])
            except ImportError:          # kiwipiepy 없으면 글자 TF-IDF 만
                self._bm = None
        return self._bm

    # ---------- 답변 문장 만들기 ----------
    def answer(self, query, hits, max_sentences=2, rows=None):
        """ANTHROPIC_API_KEY 가 있으면 LLM으로 '근거 안에서만' 쉬운 문장 생성,
        없으면 검색 chunk에서 질문과 가장 비슷한 문장을 그대로 추출(환각 없음)."""
        from . import guard
        if os.environ.get("ANTHROPIC_API_KEY") and guard.llm_budget():   # 하루 한도를 넘으면 추출식으로 (ksl/guard.py)
            try:
                out = self._llm_answer(query, hits, max_sentences)
                LLM_STATUS["last"] = "정상"
                LLM_STATUS["ok"] += 1
                return out, "llm"
            except Exception as e:  # LLM 실패 시 추출식으로 대체
                import time
                LLM_STATUS["last"] = LLM_STATUS["last_error"] = f"오류: {str(e)[:200]}"
                LLM_STATUS["fail"] += 1
                LLM_STATUS["last_error_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[rag] LLM 실패 → 추출식 사용: {e}")
        return self._extractive(query, hits, max_sentences, rows), "extractive"

    def _extractive(self, query, hits, n, rows=None):
        """검색된 chunk 의 문장(목록 항목 포함) 중 질문과 가장 맞는 문장을 그대로 뽑음.
        점수 = 문장-질문 유사도 + 상위 chunk 가산점 (검색 순위를 존중).
        rows(hit) → (그 쪽 표의 줄을 문장으로 만든 것, 표 안의 줄인지 판단) 이 있으면, 칸이 뒤섞인 표 글 대신 그 문장을 후보로 씀."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from .rowtext import list_item
        cand, seen, rowset = [], set(), set()
        for rank, h in enumerate(hits):
            made, in_table = rows(h) if rows else ([], lambda s: False)
            for s in made:
                if s not in seen:
                    seen.add(s)
                    cand.append((s, rank))
                    rowset.add(s)
            for s in h["text"].split("\n"):
                s = s.strip()
                s = list_item(s) or s                      # '○○ 작업: 보호구' → 문장
                if made and in_table(s):
                    continue
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
        sc = sc + np.array([0.08 if s_ in rowset else 0.0 for s_, _ in cand])   # 표 줄로 만든 문장은 완성된 문장 → 제목 줄보다 먼저
        top = np.argsort(-sc)[:n]
        top = [i for i in top if sc[i] >= 0.75 * sc[top[0]]]  # 1위보다 많이 낮은 문장은 버림
        return [sents[i] for i in top]

    def _llm_answer(self, query, hits, n):
        ctx = "\n".join(f"[{h['doc']} p.{h['page']}] {h['text']}" for h in hits)
        prompt = (
            "아래 산업안전 문서 내용만 근거로 질문에 답하세요. 문서에 없는 내용은 만들지 마세요.\n"
            f"- 수어로 번역할 것이므로 짧고 쉬운 평서문 {n}문장 이내\n"
            "- 각 문장은 줄바꿈으로 구분, 번호·기호 없이\n"
            "- 각 문장은 그 문장만 읽어도 뜻이 통하게: 무엇을(보호구 이름)·어떤 작업에서인지를 빼지 않기\n"
            "- 종류·등급·차이를 묻거나 문서에 여러 개가 나열되어 있으면 하나만 고르지 말고 한 문장에 모두 나열\n"
            "- 질문이 두 가지를 물으면(예: 온도와 습도) 둘 다 답하기\n"
            "- 질문한 작업·상황에 대한 내용만 답하기. 질문과 상관없는 일반 문장은 덧붙이지 않기\n"
            "- 질문에 딱 맞는 수치·표현이 없어도 문서에 관련 규정이 있으면 문서에 적힌 그대로 답하기 (예: '충분한 양을 갖추어야 합니다')\n"
            "- 문서에 관련 내용이 전혀 없을 때만 '문서에서 찾을 수 없습니다' 한 줄만\n\n"
            f"[문서]\n{ctx}\n\n[질문]\n{query}"
        )
        key = (query.strip(), tuple((h["page"], h["text"][:40]) for h in hits), n)
        if key in _LLM_CACHE:                                   # 같은 질문은 다시 부르지 않음 (바로 답 + 비용 0)
            return list(_LLM_CACHE[key])
        # 연결 재사용 → 질문마다 접속하는 시간 절약. 429·5xx·연결 오류는 SDK 가 2번까지 다시 시도
        r = client().messages.create(
            model=os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        if r.stop_reason == "refusal":                          # 거절 → answer() 가 추출식으로 대체
            raise RuntimeError("Claude 가 답변을 거절함")
        text = "".join(b.text for b in r.content if b.type == "text")
        LLM_STATUS["raw"] = text.strip()[:300]
        LLM_STATUS["pages"] = [h["page"] for h in hits]
        out = [s.strip() for s in text.split("\n") if s.strip()][:n]
        if len(_LLM_CACHE) > 500:
            _LLM_CACHE.clear()
        _LLM_CACHE[key] = out
        return list(out)


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
