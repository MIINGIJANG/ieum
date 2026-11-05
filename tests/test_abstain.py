"""환각 방지: 문서 밖 입력은 답하지 않고, 문서 안 질문은 답함 (docs/hallucination_guard.md, 기준값 0.11)."""
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
