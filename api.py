"""이음 FastAPI 서버 — 웹 화면(React/Three.js 또는 web/index.html)과 붙는 지점.

KSL_CKPT=checkpoints/run1/best.pt KSL_INDEX=checkpoints/kosha_index.pkl \
KSL_LEXICON=data/sign/lexicon.csv KSL_MOTION=data/sign/motions KSL_SENTENCES=data/sign/demo_sentences.json \
uvicorn api:app --port 8000

브라우저: http://localhost:8000        (시연용 웹 화면)
          http://localhost:8000/docs   (API 테스트)
명세: docs/api.md
"""
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ksl import guard, webfiles
from ksl.logsync import LogSync
from ksl.pipeline import SafetySignPipeline


class UTF8JSON(JSONResponse):
    media_type = "application/json; charset=utf-8"   # 브라우저(Safari 등)에서 한글이 깨지지 않게


@asynccontextmanager
async def lifespan(app):
    _warm_on_start()
    if not os.environ.get("KSL_NO_WARMUP"):
        webfiles.warm(WEB)     # 웹 파일 목록·압축본을 뒤에서 미리 (첫 방문자가 압축을 기다리지 않게)
    sync.start()               # 수어 없는 단어 기록: 예전 서버의 기록 받기 + 10분마다 올리기 (ksl/logsync.py)
    yield
    sync.stop()


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
app = FastAPI(title="이음 — 산업안전 수어 안내 API", version="0.4.0", default_response_class=UTF8JSON, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)   # 답·동작 JSON 압축. 웹 파일(web/)은 미리 압축해 둔 것을 보냄 (ksl/webfiles.py)


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
    os.environ.get("KSL_LEXICON", "data/sign/lexicon.csv"),
    os.environ.get("KSL_MOTION", "data/sign/motions"),
    os.environ.get("KSL_SENTENCES", "data/sign/demo_sentences.json"),
)
sync = LogSync(pipe.missing)


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


TOO_MANY = JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": "60"})


@app.post("/ask")
def ask(req: Ask, request: Request):
    if not guard.allow("ask", guard.who(request), 20):
        return TOO_MANY
    return pipe.ask(req.question[:guard.MAX_TEXT], min(max(req.beam, 1), 8))


class Query(BaseModel):
    text: str
    beam: int = 4
    prefetch: bool = False     # 로딩 화면에서 예시 질문 답을 미리 받아 둘 때 (수어 없는 단어 기록은 실제로 물어볼 때)
    seen: bool = False         # 미리 받아 둔 답을 화면에 보였을 때: 기록만 하고 답은 다시 보내지 않음


_QCACHE = {}                   # 답 캐시: (질문, beam) → (응답, 수어 없는 단어 기록). 같은 질문은 바로 답
_QCACHE_MAX = 300


class TooMany(Exception):
    pass


def cached_query(text, beam=4, log=True, who=None):
    """who 가 있으면(바깥에서 온 요청) 새 질문만 요청 한도에 셈 — 캐시에 있는 질문은 비용이 없으므로 세지 않음."""
    import contextlib
    from ksl.pipeline import clean_spoken
    text, beam = text[:guard.MAX_TEXT], min(max(beam, 1), 8)
    key = (clean_spoken(text).strip(), beam)
    hit = _QCACHE.get(key)
    if hit is None:
        if who is not None and not guard.allow("query", who, 20):
            raise TooMany()
        cap = pipe.missing.capture() if pipe.missing is not None else contextlib.nullcontext([])
        with cap as calls:
            r = pipe.query(text, beam)
        hit = (r, calls)
        while len(_QCACHE) >= _QCACHE_MAX:
            _QCACHE.pop(next(iter(_QCACHE)), None)
        _QCACHE[key] = hit
    if log and hit[1] and pipe.missing is not None:
        pipe.missing.replay(hit[1])
    return hit[0]


@app.post("/query")
def query(req: Query, request: Request):
    """검색창 하나: 모든 입력을 질문으로 보고 문서 검색 후 답변. 답하지 않으면 mode 가 none.
    같은 곳에서 새 질문은 1분에 20개까지 (ksl/guard.py)."""
    try:
        r = cached_query(req.text, req.beam, log=not req.prefetch, who=guard.who(request))
    except TooMany:
        return TOO_MANY
    return {"ok": True} if req.seen else r


@app.get("/demo_sentences")
def demo_sentences():
    """조원이 확정한 안내 문장 목록 (웹 예시 버튼용)."""
    return [{"korean": d["korean"], "gloss": d["gloss"], "complete": d.get("complete", True)} for d in pipe.demo]


_EXAMPLES = None


_STARTED = time.time()


@app.get("/llm_check")
def llm_check():
    """Claude(LLM 답변 모드) 연결 확인: 브라우저로 이 주소를 열면 됨. 질문 1번만큼 비용(약 3원)."""
    from ksl.rag import LLM_STATUS
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"연결": "안 됨", "이유": "ANTHROPIC_API_KEY 가 설정되지 않음 (Space Settings → Variables and secrets)"}
    r = pipe.ask("정화통은 언제 교체해요?")
    ok = r.get("answer_mode") == "llm"
    raw = os.environ["ANTHROPIC_API_KEY"]
    # 설정 확인용 (글자는 하나도 내보내지 않음): 길이, 모양이 맞는지, 공백·따옴표가 섞였는지, 서버가 켜진 지 몇 분인지
    info = {"길이": len(raw.strip()), "sk-ant- 로 시작": raw.strip().strip("\"'").startswith("sk-ant-"),
            "공백·따옴표 섞임": raw != raw.strip().strip("\"'").strip(), "서버 켜진 지(분)": round((time.time() - _STARTED) / 60)}
    return {"연결": "됨" if ok else "안 됨", "답변 방식": r.get("answer_mode"), "설정": info,
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
            cached_query("낙하물은 어떻게 조심해요?", log=False)       # 검색·출처·동작 합치기
        except Exception as e:
            print("[이음] 미리 실행 실패:", e)
        _WARM["done"] = True
    return {"ok": True, "ms": round((time.time() - t0) * 1000)}


def _warm_on_start():
    """서버가 켜질 때(lifespan, 메인 스레드) 미리 준비 → 첫 방문자의 로딩 화면이 서버를 기다리지 않음.
    KSL_NO_WARMUP=1 이면 건너뜀 (테스트용)."""
    if os.environ.get("KSL_NO_WARMUP"):
        return
    warmup()
    # 예시 질문의 답을 뒤에서 미리 만들어 둠 → 예시 질문을 누르면 바로 답
    # (Claude 답변 모드면 서버가 켜질 때 1번, 약 40원. 번역 모델은 위에서 메인 스레드로 이미 불러옴)
    import threading

    def prefetch():
        for e in examples():
            try:
                cached_query(e["question"], log=False)
            except Exception as err:
                print("[이음] 예시 답 미리 만들기 실패:", err)
        # 그다음 모든 낱말 동작을 미리 정리해 둠 (ksl/motion.py _clean_clip) → 처음 나오는 낱말도 바로.
        # 사이사이 잠깐 쉬어서 그동안 들어온 질문이 먼저 처리되게
        import time
        from ksl.motion import _clean_clip
        if pipe.motion:
            for g in list(pipe.motion.index):
                try:
                    clip = pipe.motion.get(g)
                    if clip and clip.get("frames"):
                        _clean_clip(clip, clip.get("fps", 30))
                except Exception as err:
                    print("[이음] 동작 미리 정리 실패:", g, err)
                time.sleep(0.05)
    threading.Thread(target=prefetch, daemon=True).start()


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


@app.get("/blocks_check")
def blocks_check():
    """수어 블록 점검: 예시 질문마다 답변·블록을 이어 읽은 문장·점검 결과 (web/blocks.html 이 표로 보여줌).
    답은 캐시(서버가 켜질 때 미리 만든 예시 답)를 쓰므로 보통 비용 없음. 수어 없는 단어 기록은 하지 않음."""
    from ksl.keypoints import check_blocks
    rows = []
    for e in examples():
        out = cached_query(e["question"], log=False)
        sents = out.get("sentences") or []
        units = [{"word": w, "gloss": g} for s_ in sents for g, w in zip(s_["gloss"], s_.get("words") or s_["gloss"]) if w]
        method = sents[0].get("method") if sents else None
        hint = sents[0]["korean"] if method == "keypoints_sign" else None
        rows.append({"question": e["question"], "answer": out.get("answer_text", ""), "answer_mode": out.get("answer_mode"),
                     "blocks": [u["word"] for u in units], "method": method, "debug": out.get("sign_debug"),
                     "problems": check_blocks(units, hint) if units else ["블록 없음"]})
    return {"rows": rows, "ok": sum(not r["problems"] for r in rows), "total": len(rows)}


@app.get("/missing")
def missing(n: int = 100):
    """수어 동작이 없어 회색(또는 지문자)으로 나온 단어: 많이 나온 순서 (ksl/missing.py). 다음에 추가할 수어 고르기용."""
    items = pipe.missing.top(n)
    return {"total_words": len(pipe.missing.data), "total_count": sum(v["count"] for v in pipe.missing.data.values()), "items": items,
            "substitutes": pipe.missing.top(n, subs=True),
            "sync": sync.status}                                      # 기록 보관(HF 데이터셋) 상태   # 대체 표현으로 보여준 단어 (data/sign/substitutes.csv, 검수 필요)


@app.post("/motion")
def motion(req: Glosses):
    return pipe.motion_for(req.glosses[:guard.MAX_GLOSSES], log=False) or {"fps": 30, "frames": [], "segments": []}


_WCACHE = {}                   # 단어 하나 동작 (손 올리기 → 단어 → 손 내리기) 캐시


@app.post("/motions")
def motions(req: Glosses):
    """단어마다 따로 만든 동작을 한 번에: 답이 나오면 화면이 미리 받아 둠 → 단어를 누르면 바로 다시 보기."""
    out = {}
    for g in dict.fromkeys(req.glosses[:40]):
        if g not in _WCACHE:
            if len(_WCACHE) >= 500:
                _WCACHE.pop(next(iter(_WCACHE)), None)
            _WCACHE[g] = pipe.motion_for([g], log=False) or {"fps": 30, "frames": [], "segments": []}
        out[g] = _WCACHE[g]
    return out


@app.get("/offline/manifest")
def offline_manifest():
    """오프라인 저장용 웹 파일 목록: 파일마다 내용 해시 (ksl/webfiles.py). 화면(web/sw.js)이 저장해 둔 것과 같은지 확인하는 데 씀."""
    return JSONResponse(webfiles.manifest(WEB), headers={"Cache-Control": "no-store"})


@app.post("/client/log")
async def client_log(request: Request):
    """화면에서 일어난 오류·첫 로딩 시간 기록 (ksl/clientlog.py). 주소(IP)·위치·질문 내용은 남기지 않음."""
    from ksl import clientlog
    try:
        raw = await request.body()
        if len(raw) <= 4000:
            import json as _json
            clientlog.log(_json.loads(raw), request.headers.get("user-agent", ""))
    except Exception:
        pass                              # 기록 실패는 화면에 영향 없음
    return Response(status_code=204)


@app.get("/client/status")
def client_status():
    """화면 쪽 기록 확인용: 기기 분류별 첫 로딩 시간(가운데 값·가장 긴 값), 오류 건수, 최근 오류 50건. 서버가 다시 켜지면 처음부터."""
    from ksl import clientlog
    return {**clientlog.status(), "guard": guard.status()}


@app.get("/sos/config")
def sos_config():
    """SOS 준비 상태: 지도 표시용 카카오 JavaScript 키(공개용, 도메인으로 보호)와 서버 키가 있는지."""
    from ksl import sos
    return sos.configured()


@app.get("/sos/nearest")
def sos_nearest(request: Request, lat: float, lng: float, fresh: int = 0):
    """지금 위치에서 가장 가까운 응급실 + 자동차 경로 + 실시간 병상 (ksl/sos.py). 위치는 저장하지 않음."""
    from ksl import sos
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return JSONResponse({"error": "bad_location"}, status_code=422)
    if not guard.allow("sos", guard.who(request), 12):       # 공공데이터·카카오 한도 보호: 같은 곳에서 1분에 12번까지
        return TOO_MANY
    if not sos.configured()["er"]:
        sos.log_error("config", "DATA_GO_KR_KEY 없음")
        return JSONResponse({"error": "not_configured"}, status_code=503)
    try:
        return sos.nearest(lat, lng, fresh=bool(fresh))
    except Exception:            # 실패 내용은 sos.log_error 가 기록함 (/sos/status)
        return JSONResponse({"error": "failed"}, status_code=502)


@app.get("/sos/status")
def sos_status():
    """SOS 사용 기록·오류 확인용 (서버가 다시 시작하면 처음부터): 요청 수·성공·실패·단계별 실패·평균 시간·최근 오류 30건.
    위치·좌표는 남기지 않음. 브라우저로 이 주소를 열면 됨."""
    from ksl import sos
    return sos.status()


# 웹 화면 (web/index.html, 3D 모델·라이브러리 포함). API 경로보다 뒤에 둬야 함
app.mount("/", webfiles.WebFiles(directory=WEB, html=True), name="web")
