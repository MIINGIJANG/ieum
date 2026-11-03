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
