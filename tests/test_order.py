"""한국수어 어순과 문법 비수지 표현 (ksl/order.py)."""
import pytest

from ksl.pipeline import SignWords

sw = SignWords()


def glosses(s):
    return [u["gloss"] for u in sw.analyze(s)]


def roles(s, gloss):
    return next(u["nms"]["roles"] for u in sw.analyze(s) if u["gloss"] == gloss and u["nms"])


@pytest.mark.parametrize("sentence, expected", [
    ("보호구를 착용하지 않으면 다칠 수 있습니다.", ["보호1", "입다1", "안하다", "부상1", "가능1"]),   # 부정·가능은 동사 뒤
    ("작업할 때 안전모를 반드시 착용해야 한다.", ["일하다1", "때2", "안전모", "입다1", "꼭1", "필요1"]),  # 꼭 필요 (조원 샘플 어순)
    ("정화통은 6개월마다 교체해야 합니다.", ["6개월", "정화통", "바꾸다1", "필요1"]),                # 시간은 앞으로
    ("잠깐이라도 보호구를 벗지 마세요.", ["잠깐", "보호1", "벗다", "금지"]),                          # -지 마세요 → 금지
    ("방진마스크를 안 쓰면 위험합니다.", ["마스크1", "입다1", "안하다", "위험1"]),                  # '안 쓰다' → 쓰다 안하다
    ("보호구를 착용하지 않으면 안 됩니다.", ["보호1", "입다1", "필요1"]),                            # 이중 부정 = 필요
    ("신규 근로자는 4시간 이상 교육을 받아야 합니다.", ["4시간", "이상", "새롭다", "사람1", "교육", "받다", "필요1"]),
    ("사용하지 않을 때는 이상이 없는지 검사하세요.", ["사용1", "안하다", "때2", "이상하다1", "없다1", "검사1"]),
    ("보호구를 착용하지 않은 근로자는 작업할 수 없습니다.", ["보호1", "입다1", "안하다", "사람1", "일하다1", "못하다"]),
])
def test_word_order(sentence, expected):
    assert glosses(sentence) == expected


def test_team_sentence_order_kept():
    """조원이 확정한 문장과 같은 순서 (낱말이 같은 부분)."""
    assert glosses("높은 곳에서 작업할 때는 안전대를 사용하세요.") == ["높다", "장소1", "일하다1", "때2", "안전벨트0", "사용1"]


def test_grammar_nms():
    s = "방진마스크를 안 쓰면 위험합니다."
    assert roles(s, "입다1") == ["neg", "cond"]           # 동작 있는 동사 위에서 고개 젓기 + 조건절 눈썹
    assert "cond" not in (sw.analyze(s)[-1]["nms"] or {}).get("roles", [])   # '위험합니다' 는 조건절 밖
    u = next(u for u in sw.analyze("정화통은 6개월마다 교체해야 합니다.") if u["gloss"] == "필요1")
    assert u["nms"]["head"]["type"] == "nod"
    neg = next(u for u in sw.analyze("보호구를 착용하지 않으면 다칠 수 있습니다.") if u["gloss"] == "안하다")
    assert neg["nms"]["head"]["type"] == "shake"


def test_words_shown_as_in_answer():
    """화면 단어 블록: 답변에 쓰인 모양 그대로, 넣은 문법 단어는 뜻으로."""
    u = sw.analyze("보호구를 착용하지 않으면 다칠 수 있습니다.")
    assert [x["word"] for x in u] == ["보호구를", "착용하지", "안 함", "다칠", "할 수 있음"]
