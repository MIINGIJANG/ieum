"""서버 API: 켜지는지, 질문·문장·동작 응답 형식 (번역 모델 없이)."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(index_path):
    os.environ.update(KSL_INDEX=str(index_path), KSL_CKPT="no-model.pt", KSL_NO_WARMUP="1")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    import api
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


def test_health(client):
    r = client.get("/health").json()
    assert r["ok"] and r["chunks"] > 50 and r["motion_glosses"] > 50


def test_query_ask(client):
    r = client.post("/query", json={"text": "보호구 보관할 때 온도랑 습도는?"}).json()
    assert r["mode"] == "ask" and r["sources"]
    seg = r["motion"]["segments"]
    assert len(seg) == sum(len(s["gloss"]) for s in r["sentences"])
    assert all("nms" in x for x in seg)
    assert r["motion"]["frames"]


def test_query_refuses_chitchat(client):
    assert client.post("/query", json={"text": "안녕하세요"}).json()["mode"] == "none"


def test_motion(client):
    r = client.post("/motion", json={"glosses": ["위험1", "없는단어"]}).json()
    assert r["frames"] and [s["has_motion"] for s in r["segments"]] == [True, False]   # 지문자는 기본 꺼짐 → 회색


def test_missing(client):
    client.post("/query", json={"text": "방독마스크 정화통은 언제 교체해야 하나요?"})
    r = client.get("/missing").json()
    assert r["total_words"] > 0 and r["items"] and r["items"][0]["count"] >= 1
    assert all("gloss" in x and "examples" in x for x in r["items"])


def test_utf8_header(client):
    assert "charset=utf-8" in client.get("/health").headers["content-type"]


def test_query_prefetch_cache(client):
    q = "보안경은 언제 써야 해요?"
    before = client.get("/missing").json()["total_count"]
    a = client.post("/query", json={"text": q, "prefetch": True}).json()     # 미리 받기: 기록 안 함
    assert client.get("/missing").json()["total_count"] == before
    assert client.post("/query", json={"text": q, "seen": True}).json() == {"ok": True}   # 보였을 때: 기록만
    b = client.post("/query", json={"text": q}).json()                        # 같은 질문은 캐시
    assert a == b


def test_motions_batch(client):
    r = client.post("/motions", json={"glosses": ["위험1", "위험1", "없는단어"]}).json()
    assert set(r) == {"위험1", "없는단어"}                                  # 같은 단어는 한 번
    assert r["위험1"]["frames"] and r["위험1"]["segments"][0]["has_motion"]
    assert not r["없는단어"]["segments"][0]["has_motion"]


def test_blocks_check(client):
    r = client.get("/blocks_check").json()
    assert r["total"] == len(r["rows"]) > 0 and all("problems" in x and "blocks" in x for x in r["rows"])


def test_sos_not_configured(client, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_KEY", raising=False)
    assert client.get("/sos/nearest", params={"lat": 35.8, "lng": 128.5}).status_code == 503
    assert client.get("/sos/config").json()["er"] is False
    st = client.get("/sos/status").json()
    assert st["failed"] >= 1 and st["recent_errors"][0]["stage"] == "config"


def test_web_files_precompressed(client):
    """웹 파일은 미리 압축해 둔 것을 보내고, 바뀌지 않았으면 304 (ksl/webfiles.py)."""
    import gzip
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "web/sign/core.js").read_bytes()
    r = client.get("/sign/core.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-cache"
    assert r.content == src and int(r.headers["content-length"]) < len(src)            # 클라이언트가 풀어 준 내용 = 원본
    again = client.get("/sign/core.js", headers={"Accept-Encoding": "gzip", "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    plain = client.get("/sign/core.js", headers={"Accept-Encoding": "identity"})        # 압축을 못 받는 클라이언트는 원본
    assert plain.status_code == 200 and "content-encoding" not in plain.headers and plain.content == src
    png = client.get("/favicon-32.png", headers={"Accept-Encoding": "gzip"})            # 이미 압축된 형식은 그대로
    assert png.status_code == 200 and "content-encoding" not in png.headers and png.headers["cache-control"] == "no-cache"
    assert gzip.decompress(gzip.compress(src)) == src
    assert client.get("/", headers={"Accept-Encoding": "gzip"}).headers["content-type"].startswith("text/html")


def test_offline_manifest(client):
    """오프라인 저장용 파일 목록: 파일마다 해시, 미리 저장할 목록에서 점검 페이지·안 쓰는 모델은 뺌."""
    m = client.get("/offline/manifest")
    assert m.headers["cache-control"] == "no-store"
    m = m.json()
    files, pre = m["files"], set(m["precache"])
    assert files["/"] == files["/index.html"] and len(m["version"]) == 12
    for need in ["/", "/sos.js", "/sign/app.js", "/sign/core.js", "/sign/popup.css", "/sign/models/model.onnx", "/sign/models/hand_landmarker.task",
                 "/sign/lib/ort/ort-wasm-simd-threaded.wasm", "/models/avatar.glb", "/vendor/three/three.module.js"]:
        assert need in pre, need
    for skip in ["/sign/models/holistic_landmarker.task", "/models/avatar.vrm", "/blocks.html", "/sign/eval.html"]:
        assert skip in files and skip not in pre, skip
    assert all(u in files for u in pre)
