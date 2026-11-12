"""표의 한 줄 → 문장 (Claude 없이 답할 때). 표에 적힌 말만 쓰고 조사·끝맺음만 붙이는지."""
from ksl.rowtext import josa, list_item, row_sentences


def test_josa():
    assert josa("안전대", "을를") == "안전대를" and josa("보안경", "을를") == "보안경을"
    assert josa("귀마개 2종(EP-2)", "은는") == "귀마개 2종(EP-2)은"


def test_work_table():
    rows = [["작 업 명", "보호구", "보호대상"],
            ["높이 또는 깊이 2미터 이상의 떨어질 위험이 있는 장소에서 하는 작업", "안전대(安全帶)", "몸"],
            ["밀폐공간 작업", "공기호흡기 또는 송기마스크, 사다리, 섬유로프 등", "몸"],
            ["폭염작업 1. 바닥 면으로부터 … 2. …", "개인용 냉방 또는 통풍장치를 지급ㆍ가동하거 나 개인용 보냉장구를 지급ㆍ착용", "몸"]]
    s = [x for x, _ in row_sentences(rows)]
    assert s[0] == "높이 또는 깊이 2미터 이상의 떨어질 위험이 있는 장소에서 하는 작업에는 안전대를 착용합니다."
    assert s[1].endswith("등을 사용합니다.")                       # 사다리·로프는 '착용'이 아님
    assert s[2].startswith("폭염작업에 필요한 보호구: ")             # 문장으로 만들기 어려우면 표의 말 그대로


def test_term_table():
    rows = [["종 류", "성 능 구 분"], ["정전기안전화", "발을 보호하고 정전기의 인체대전을 방지하기 위한 것"], ["벨 트 식", "1개 걸이용 안전대"]]
    assert [x for x, _ in row_sentences(rows)] == ["정전기안전화는 발을 보호하고 정전기의 인체대전을 방지하기 위한 것입니다."]
    assert row_sentences([["등 급", "특 급"], ["사용장소", "…"]]) == []   # 모르는 모양의 표는 건드리지 않음


def test_list_item():
    assert list_item("물체가 흩날릴 위험이 있는 작업: 보안경") == "물체가 흩날릴 위험이 있는 작업에는 보안경을 착용합니다."
    assert list_item("보호구는 수시로 점검한다.") is None
