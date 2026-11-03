"""음성 입력처럼 바뀐 글: 군말 빼기, 끝맺음 바꿔 확정 안내 문장 찾기, 띄어쓰기 붙여 다시 검색."""
from ksl.pipeline import clean_spoken, ending_variants


def test_clean_spoken():
    assert clean_spoken("어 위험한 음 곳에서는 작업을 중지하세요") == "위험한 곳에서는 작업을 중지하세요"
    assert clean_spoken("음 어떻게 해요") == "어떻게 해요"            # '어떻게' 는 그대로
    assert clean_spoken("어") == "어"


def test_ending_variants():
    assert "위험한 곳에서는 작업을 중지하세요" in ending_variants("위험한 곳에서는 작업을 중지해야 돼")
    assert "기계를 멈추세요" in ending_variants("기계를 멈춰야 돼")
    assert "기계 사용을 금지합니다" in ending_variants("기계 사용을 금지해요")


def test_voice_like_queries(pipe):
    clean = pipe.query("위험한 곳에서는 작업을 중지하세요.")
    for v in ("어 위험한 음 곳에서는 작업을 중지하세요", "위험한 곳에서는 작업을 중지해야 돼"):
        r = pipe.query(v)
        assert r["mode"] == "translate" and r["sentences"][0]["gloss"] == clean["sentences"][0]["gloss"]
    assert pipe.query("정전기 안전화는 어떤 역할을 해요")["mode"] == "ask"   # 문서는 '정전기안전화' 로 붙여 씀


def test_substitute_and_short_sign(pipe):
    """동작 없는 단어는 대체 표현으로(기록), 답변 수어는 짧게 (확정 문장 또는 첫 문장)."""
    r = pipe.sign_sentence("안전모를 쓰세요.")
    assert r["gloss"][:2] == ["머리0", "보호1"] and r["sub_of"][:2] == ["안전모", "안전모"]
    assert any(x["gloss"] == "안전모" for x in pipe.missing.top(subs=True))
    a = pipe.query("보호구에 이상이 있는지 어떻게 확인해요?")
    assert a["answer_mode"] == "demo" and a["sentences"][0]["gloss"] == ["보호1", "이상하다1", "없다1", "확인1"]   # 보호구 수어 포함
    b = pipe.query("정화통은 언제 교체해요?")
    sg = b["sentences"][0]
    assert len(b["sentences"]) == 1 and sg["method"].startswith("keypoints_") and sg["korean"] in b["answer_text"]
    assert len(sg["gloss"]) <= 9 and any("6개월" in (w or "") for w in sg["words"])   # 핵심 단어만, '언제' 질문은 기간 포함


def test_keypoints_llm_only_uses_answer_words(pipe, monkeypatch):
    """Claude 로 핵심 고르기: 답변에 없는 말은 버림, 결과가 모자라면 규칙 기반으로 (실제 API 대신 가짜 응답)."""
    import json as _json
    import types
    import anthropic
    from ksl import keypoints

    answer = "정화통은 6개월마다 교체하고, 보관 환경은 온도 5~30℃를 유지해야 한다."
    reply = {"key_sentence": "정화통은 6개월마다 교체하고", "keywords": ["정화통", "6개월", "교체", "우주선"]}

    class Fake:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema" and kw["fallbacks"] == "default"
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=_json.dumps(reply, ensure_ascii=False))])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    r = keypoints.extract(pipe, "정화통은 언제 교체해요?", answer, [answer])
    assert r["method"] == "llm" and "우주선" not in [u["word"] for u in r["units"]]
    reply["keywords"] = ["우주선", "로켓"]                                 # 답변에 없는 말뿐 → 규칙 기반
    assert keypoints.extract(pipe, "정화통은 언제 교체해요?", answer, [answer])["method"] == "rule"
