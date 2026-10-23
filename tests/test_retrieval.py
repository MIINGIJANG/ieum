"""KOSHA 검색 성능이 떨어지지 않았는지 (Recall@3). 기준값은 지금 성능보다 조금 낮게 잡아 작은 흔들림은 허용.
heldout3 은 검색 개선(절 제목·이웃 chunk·현장 말 확장) 전에 만든 질문이라 공정한 비교용."""
import pytest

from ksl.rag import is_hit, load_questions

from conftest import ROOT

# 질문 세트: 최소 Recall@3 (2026-10 측정: 1.000 / 1.000 / 0.950 / 0.773)
FLOORS = {"eval": 0.95, "heldout": 0.9, "heldout2": 0.85, "heldout3": 0.7}


@pytest.mark.parametrize("name", FLOORS)
def test_recall_at_3(rag, name):
    qs = load_questions(ROOT / f"data/eval/{name}_questions.csv")
    hit = sum(any(is_hit(c, q) for c in rag.search(q["question"], 3)) for q in qs)
    assert hit / len(qs) >= FLOORS[name], f"{name} Recall@3 = {hit}/{len(qs)}"


def test_sections_detected(rag):
    """절 제목: 표 칸만 있는 chunk 도 '7.2 안전화의 종류' 같은 문맥을 가짐 (표 안 '7.녹인 금속…' 은 절 제목 아님)."""
    secs = {c["section"] for c in rag.chunks}
    assert any("7.2 안전화의 종류" in s for s in secs)
    assert any("10. 개인보호구의 유지 관리" in s for s in secs)
    assert not any("녹인 금속" in s for s in secs)


def test_compound_backoff(rag):
    """문서에 없는 합성어는 앞부분으로 찾음 ('잠수부' → 잠수작업이 있는 10쪽)."""
    assert 10 in [h["page"] for h in rag.search("물속에서 일하는 잠수부 장비", 3)]
