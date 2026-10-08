"""환각 방지(답변 거절) 평가.

  python scripts/eval_abstain.py --ckpt checkpoints/test/best.pt

- 문서 안 질문(dev 30 + heldout 10): 답을 하는 비율, 답한 것 중 정답 쪽이 출처 3개 안에 있는 비율
- 문서 밖 입력(인사·잡담·문서에 없는 안전 주제·무관한 문장 28개): 답하지 않는 비율
- 안전 안내 문장 10개: 번역(수어)을 하는 비율
비교: 거절 기능 끈 상태(min_score=0, 핵심어 검사 없음) vs 켠 상태
"""
import argparse
import csv
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ksl.pipeline import SafetySignPipeline  # noqa: E402

K = "data/kosha"
rows = lambda f: list(csv.DictReader(open(f"{K}/{f}", encoding="utf-8")))


def run(p, label):
    inq = rows("eval_questions.csv") + rows("heldout_questions.csv")
    answered = hit = 0
    for r in inq:
        out = p.query(r["question"])
        if out["mode"] != "none":
            answered += 1
            if r["gold_page"] and any(s["page"] == int(r["gold_page"]) for s in out.get("sources", [])):
                hit += 1
    gold = sum(1 for r in inq if r["gold_page"])
    oos = rows("out_of_scope_questions.csv")
    refused = [r for r in oos if p.query(r["question"])["mode"] == "none"]
    leaked = [r["question"] for r in oos if r not in refused]
    sents = rows("safety_sentences.csv")
    translated = sum(1 for r in sents if p.query(r["sentence"])["mode"] == "translate")
    print(f"\n[{label}]")
    print(f"  문서 안 질문 {len(inq)}개: 답함 {answered} ({answered/len(inq):.0%}), "
          f"정답 쪽이 출처에 있음 {hit}/{gold}")
    print(f"  문서 밖 입력 {len(oos)}개: 답하지 않음 {len(refused)} ({len(refused)/len(oos):.0%})"
          + (f"  ← 새어 나간 것: {leaked}" if leaked else ""))
    print(f"  안전 안내 문장 {len(sents)}개: 번역함 {translated}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=os.environ.get("KSL_CKPT", "checkpoints/test/best.pt"))
    a = ap.parse_args()
    args = (a.ckpt, "checkpoints/kosha_index.pkl", "data/lexicon.csv", "data/motions", "data/demo_sentences.json")
    run(SafetySignPipeline(*args, min_score=0.0, domain_terms=None), "거절 기능 끔 (조치 전)")
    run(SafetySignPipeline(*args), "거절 기능 켬 (조치 후)")
