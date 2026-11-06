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
