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

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ksl.pipeline import SafetySignPipeline

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="이음 — 산업안전 수어 안내 API", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
    return {"ok": True, "chunks": len(pipe.rag.chunks), "gloss_vocab": len(pipe.tr.tgt),
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


@app.get("/source_image")
def source_image(doc: str, page: int, x0: float, y0: float, x1: float, y1: float):
    """출처 영역을 원본 PDF 모양 그대로 잘라낸 PNG."""
    try:
        png = pipe.images.render(doc, page, (x0, y0, x1, y1))
    except Exception:
        png = None
    if not png:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


_EXAMPLES = None


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
