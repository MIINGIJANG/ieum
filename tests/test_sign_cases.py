"""수어 블록 회귀 사례 (팀원 SignBridge 의 check_translation_cases 처럼): 고친 것이 다시 깨지지 않게.

규칙 기반(Claude 없이) 단어 뽑기 결과로 확인. 사이트의 Claude 수어 문장과는 다를 수 있음."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def blocks(s):
    """화면 블록 (같은 group·대체 표현은 한 블록) → [(말, [글로스...])]."""
    out = []
    for i, (g, w) in enumerate(zip(s["gloss"], s["words"])):
        if not w and out:
            out[-1][1].append(g)
        else:
            out.append((w, [g]))
    return out


CASES = [
    # (문장, 블록 말, 그 블록의 글로스)
    ("높이 2미터 이상인 곳에서는 안전대를 착용해야 합니다.", "2미터", ["수:2", "미터0"]),     # 숫자 + 단위, 블록 하나
    ("신규 근로자는 4시간 이상 교육을 받아야 합니다.", "4시간", ["시간:4시간"]),              # 소요 시간 전용 수어
    ("다른 방법들을 먼저 고려해야 합니다.", "방법들을", ["방법"]),                            # 복수 '-들'
]


@pytest.mark.parametrize("text,word,glosses", CASES)
def test_sign_cases(pipe, text, word, glosses):
    b = dict(blocks(pipe.sign_sentence(text)))
    assert b.get(word) == glosses, b


def test_no_broken_substitutes():
    """대체 표현이 가리키는 수어는 모두 동작이 있어야 함 (없으면 대체가 조용히 안 됨, scripts/eval/check_signs.py)."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts/eval"))
    from check_signs import report
    assert report()["broken"] == []
