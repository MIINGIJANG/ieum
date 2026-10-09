"""환각 방지(답변 거절) 평가.

  python scripts/eval/eval_abstain.py --ckpt checkpoints/test/best.pt

- 문서 안 질문(dev 30 + heldout 10): 답을 하는 비율, 답한 것 중 정답 쪽이 출처 3개 안에 있는 비율
- 문서 밖 입력(인사·잡담·문서에 없는 안전 주제·무관한 문장 28개): 답하지 않는 비율
- 안전 안내 문장 10개: 번역(수어)을 하는 비율
비교: 거절 기능 끈 상태(min_score=0, 핵심어 검사 없음) vs 켠 상태
"""
import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ksl.pipeline import SafetySignPipeline  # noqa: E402

from ksl.evaluation import evaluate  # noqa: E402
import json  # noqa: E402


def run(p, label):
    print(f"\n[{label}]")
    print(json.dumps(evaluate(p)["요약"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=os.environ.get("KSL_CKPT", "checkpoints/test/best.pt"))
    a = ap.parse_args()
    args = (a.ckpt, "checkpoints/kosha_index.pkl", "data/sign/lexicon.csv", "data/sign/motions", "data/sign/demo_sentences.json")
    run(SafetySignPipeline(*args, min_score=0.0, domain_terms=None), "거절 기능 끔 (조치 전)")
    run(SafetySignPipeline(*args), "거절 기능 켬 (조치 후)")
