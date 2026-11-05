"""환각 방지: 문서 밖 입력은 답하지 않고, 문서 안 질문은 답함 (docs/환각방지.md, 기준값 0.11)."""
from ksl.evaluation import _rows


def test_out_of_scope_refused(pipe):
    leaked = [r["question"] for r in _rows("out_of_scope_questions.csv") if pipe.query(r["question"])["mode"] != "none"]
    assert not leaked, f"답하면 안 되는 입력에 답함: {leaked}"


def test_in_scope_answered(pipe):
    qs = _rows("eval_questions.csv") + _rows("heldout_questions.csv")
    answered = sum(pipe.query(r["question"])["mode"] != "none" for r in qs)
    assert answered >= 38, f"문서 안 질문 {len(qs)}개 중 {answered}개만 답함"


def test_answer_has_sources_and_signs(pipe):
    r = pipe.query("정화통은 언제 교체해요?")
    assert r["mode"] == "ask" and r["answer_text"]
    assert any(s["page"] == 24 for s in r["sources"])
    assert r["sentences"] and all(len(s["gloss"]) == len(s["words"]) == len(s["nms"]) for s in r["sentences"])


def test_answer_cites_pages():
    """답변 문장마다 근거 쪽: 검색 결과와 맞는 문장은 그 쪽, 전혀 안 맞는 문장은 None."""
    from ksl.pipeline import cite_sentences
    hits = [{"page": 5, "text": "물체가 떨어지거나 날아올 위험이 있는 작업에서는 안전모를 착용해야 한다."},
            {"page": 23, "text": "교육·훈련은 신규 근로자에 대해 4시간 이상 실시한다."}]
    c = cite_sentences("신규 근로자는 4시간 이상 교육을 받아야 합니다. 오늘 날씨가 맑습니다.", hits)
    assert [x["page"] for x in c] == [23, None]
    assert cite_sentences("", hits) == []
