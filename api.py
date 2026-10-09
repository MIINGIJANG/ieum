"""이음 FastAPI 서버 — 웹 화면(React/Three.js 또는 web/index.html)과 붙는 지점.

KSL_CKPT=checkpoints/run1/best.pt KSL_INDEX=checkpoints/kosha_index.pkl \
KSL_LEXICON=data/lexicon.csv KSL_MOTION=data/motions KSL_SENTENCES=data/demo_sentences.json \
uvicorn api:app --port 8000

브라우저: http://localhost:8000        (시연용 웹 화면)
          http://localhost:8000/docs   (API 테스트)
명세: docs/API.md
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse


class UTF8JSON(JSONResponse):
    media_type = "application/json; charset=utf-8"   # 브라우저(Safari 등)에서 한글이 깨지지 않게
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ksl.pipeline import SafetySignPipeline

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="이음 — 산업안전 수어 안내 API", version="0.3.0", default_response_class=UTF8JSON)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)   # 아바타(6MB→2.5MB)·동작 JSON 압축 → 휴대폰 로딩 빠르게

INDEX = os.environ.get("KSL_INDEX", "checkpoints/kosha_index.pkl")


def _ensure_index(path):
    """KOSHA 인덱스가 없거나(처음 실행) 이 컴퓨터의 라이브러리 버전으로 못 읽으면 data/kosha/*.pdf 로 새로 만듦."""
    from ksl.rag import SafetyRAG
    try:
        SafetyRAG.load(path)
        return
    except Exception:
        pass
    pdfs = sorted(str(p) for p in (ROOT / "data" / "kosha").glob("*.pdf"))
    if pdfs:
        print(f"[이음] KOSHA 인덱스 생성: {pdfs}")
        SafetyRAG.build(pdfs, max_chars=300).save(path)


_ensure_index(INDEX)
pipe = SafetySignPipeline(
    os.environ.get("KSL_CKPT", "checkpoints/run1/best.pt"),
    INDEX,
    os.environ.get("KSL_LEXICON", "data/lexicon.csv"),
    os.environ.get("KSL_MOTION", "data/motions"),
    os.environ.get("KSL_SENTENCES", "data/demo_sentences.json"),
)


# 번역 모델(torch)은 처음 문장을 번역할 때 불러옴 (서버 시작 빠르게).
# 주의: 별도 스레드에서 미리 불러오면 Mac 의 파이썬에서 서버가 메시지 없이 꺼지는 경우가 있어 쓰지 않음


class Ask(BaseModel):
    question: str
    beam: int = 4


class Text(BaseModel):
    text: str
    beam: int = 4


class Glosses(BaseModel):
    glosses: list[str]


@app.get("/health")
def health():
    return {"ok": True, "chunks": len(pipe.rag.chunks), "gloss_vocab": len(pipe._tr.tgt) if pipe._tr else None,
            "motion_glosses": len(pipe.motion.index) if pipe.motion else 0}


@app.post("/ask")
def ask(req: Ask):
    return pipe.ask(req.question, req.beam)


class Query(BaseModel):
    text: str
    beam: int = 4


@app.post("/query")
def query(req: Query):
    """검색창 하나: 질문이면 /ask, 문장이면 /translate 처럼 동작. 응답의 mode 로 구분."""
    return pipe.query(req.text, req.beam)


@app.post("/translate")
def translate(req: Text):
    return pipe.translate_text(req.text, req.beam)


@app.get("/demo_sentences")
def demo_sentences():
    """조원이 확정한 안내 문장 목록 (웹 예시 버튼용)."""
    return [{"korean": d["korean"], "gloss": d["gloss"], "complete": d.get("complete", True)} for d in pipe.demo]


_EXAMPLES = None


@app.get("/llm_check")
def llm_check():
    """Claude(LLM 답변 모드) 연결 확인: 브라우저로 이 주소를 열면 됨. 질문 1번만큼 비용(약 3원)."""
    from ksl.rag import LLM_STATUS
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"연결": "안 됨", "이유": "ANTHROPIC_API_KEY 가 설정되지 않음 (Space Settings → Variables and secrets)"}
    r = pipe.ask("정화통은 언제 교체해요?")
    ok = r.get("answer_mode") == "llm"
    return {"연결": "됨" if ok else "안 됨", "답변 방식": r.get("answer_mode"),
            "Claude 호출 결과": LLM_STATUS["last"], "답변": r.get("answer_text"),
            "Claude 원문": LLM_STATUS.get("raw"), "보낸 문서 쪽": LLM_STATUS.get("pages")}


_EVAL = {"state": "대기", "result": None}


@app.get("/eval")
def eval_now(detail: bool = False):
    """환각 방지 평가를 이 서버(Claude 키가 있으면 Claude 답변 모드)로 한 번 실행. 브라우저로 열면 됨.
    처음 열면 뒤에서 시작(질문 약 80개, 1~3분, Claude 비용 약 250원) → 다시 열면 결과.
    한 번 끝난 결과는 서버가 다시 켜질 때까지 그대로 보여줌 (여러 번 열어도 비용 1번)."""
    import threading
    if _EVAL["state"] == "대기":
        _EVAL["state"] = "진행 중"

        def job():
            from ksl.evaluation import evaluate
            try:
                _EVAL["result"] = evaluate(pipe, detail=True)
                _EVAL["state"] = "완료"
            except Exception as e:
                _EVAL["result"], _EVAL["state"] = {"오류": str(e)}, "완료"
        threading.Thread(target=job, daemon=True).start()
    if _EVAL["state"] != "완료":
        return {"상태": "평가 진행 중입니다. 1~2분 뒤 이 페이지를 새로고침하세요.",
                "Claude 답변 모드": bool(os.environ.get("ANTHROPIC_API_KEY"))}
    r = _EVAL["result"]
    return {"상태": "완료", "Claude 답변 모드": bool(os.environ.get("ANTHROPIC_API_KEY")),
            **(r if detail else {k: v for k, v in r.items() if k != "자세히"})}


_WARM = {"done": False}


@app.post("/warmup")
def warmup():
    """첫 화면 로딩 중에 한 번 호출: 번역 모델 불러오기·검색·동작 합치기를 미리 한 번씩 돌려 둠
    → 사용자가 처음 질문하거나 문장을 넣을 때 기다림이 없게."""
    import time
    t0 = time.time()
    if not _WARM["done"]:
        try:
            pipe.translate_one("작업 전에 보호구를 점검하세요.")       # 번역 모델(torch) 불러오기
        except Exception as e:                                         # 모델이 없어도 질문 답변은 되게
            print("[이음] 번역 모델 미리 불러오기 실패:", e)
        try:
            examples()
            pipe.ask("낙하물은 어떻게 조심해요?")                      # 검색·출처·동작 합치기
        except Exception as e:
            print("[이음] 미리 실행 실패:", e)
        _WARM["done"] = True
    return {"ok": True, "ms": round((time.time() - t0) * 1000)}


@app.get("/pages")
def pages():
    """출처용 쪽 이미지 목록 (화면이 미리 받아 두어 출처가 바로 뜨게)."""
    base = ROOT / "web" / "pages"
    return [f"./pages/{d.name}/{p.name}" for d in sorted(base.iterdir()) if d.is_dir()
            for p in sorted(d.glob("*.png"), key=lambda x: int(x.stem) if x.stem.isdigit() else 0)] if base.exists() else []


@app.get("/examples")
def examples():
    """예시 질문 목록 + 주제 키. 화면은 이 중 무작위로 보여주고, 이미 한 질문과 주제가 같으면 숨김."""
    global _EXAMPLES
    if _EXAMPLES is None:
        import json
        f = ROOT / "data" / "kosha" / "example_questions.json"
        qs = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        _EXAMPLES = [{"question": q, "topic": pipe.topic(q)} for q in qs]
    return _EXAMPLES


@app.post("/motion")
def motion(req: Glosses):
    return pipe.motion_for(req.glosses) or {"fps": 30, "frames": [], "segments": []}


# 웹 화면 (web/index.html, 3D 모델·라이브러리 포함). API 경로보다 뒤에 둬야 함
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
