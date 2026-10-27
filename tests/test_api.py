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


def test_demo_sentence_uses_memory(client):
    r = client.post("/query", json={"text": "낙하물을 조심하세요."}).json()
    assert r["mode"] == "translate" and r["sentences"][0]["method"] == "memory"


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
