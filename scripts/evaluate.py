"""테스트셋 평가 + 사전 치환 베이스라인 비교 + 오류 분석 파일 생성.

python scripts/evaluate.py --run checkpoints/run1 --lexicon data/lexicon.csv
결과: <run>/eval_report.json, <run>/errors.csv
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksl.data import load_jsonl, save_json  # noqa: E402
from ksl.metrics import all_metrics  # noqa: E402
from ksl.translate import DictBaseline, Translator  # noqa: E402

AIHUB_BASELINE_BLEU = 0.1633   # AI Hub 데이터 설명서의 기준 번역 성능 (0~1 스케일)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="train.py --out 폴더")
    ap.add_argument("--lexicon", default=None)
    ap.add_argument("--split", default="test")
    ap.add_argument("--beam", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="빠른 확인용 문장 수 제한")
    a = ap.parse_args()
    run = Path(a.run)

    import json
    recs = [json.loads(l) for l in open(run / f"{a.split}.jsonl", encoding="utf-8")]
    if a.limit:
        recs = recs[: a.limit]
    refs = [r["gloss"] for r in recs]
    train_glosses = {g for l in open(run / "train.jsonl", encoding="utf-8") for g in json.loads(l)["gloss"]}

    tr = Translator(run / "best.pt", a.lexicon)
    res_model = [tr.translate(r["korean"], beam=a.beam) for r in recs]
    hyp_model = [x["gloss"] for x in res_model]
    hyp_nolex = [tr.translate(r["korean"], beam=a.beam, use_lexicon=False)["gloss"] for r in recs] if a.lexicon else None
    hyp_base = [DictBaseline(train_glosses, tr.lexicon).translate(r["korean"]) for r in recs]

    report = {
        "n_sentences": len(recs),
        "transformer": all_metrics(hyp_model, refs),
        "dict_substitution_baseline": all_metrics(hyp_base, refs),
        "aihub_reference_bleu_0to1": AIHUB_BASELINE_BLEU,
    }
    if hyp_nolex:
        report["transformer_without_lexicon"] = all_metrics(hyp_nolex, refs)

    # 테스트 정답에 있지만 학습에 없던 글로스(OOV) 비율
    test_g = [g for r in refs for g in r]
    report["test_oov_gloss_rate"] = round(sum(g not in train_glosses for g in test_g) / max(len(test_g), 1), 4)

    # 길이 구간별 BLEU (긴 문장에서 얼마나 떨어지는지)
    buckets = {"짧음(≤4 글로스)": [], "보통(5~8)": [], "김(≥9)": []}
    for h, r in zip(hyp_model, refs):
        key = "짧음(≤4 글로스)" if len(r) <= 4 else "보통(5~8)" if len(r) <= 8 else "김(≥9)"
        buckets[key].append((h, r))
    report["bleu_by_length"] = {k: (all_metrics([x[0] for x in v], [x[1] for x in v])["bleu"] if v else None)
                                for k, v in buckets.items()}

    # 자주 빠뜨리는/잘못 넣는 글로스 Top 15
    miss, extra = Counter(), Counter()
    for h, r in zip(hyp_model, refs):
        ch, cr = Counter(h), Counter(r)
        miss.update(cr - ch)
        extra.update(ch - cr)
    report["most_missed_gloss"] = miss.most_common(15)
    report["most_extra_gloss"] = extra.most_common(15)

    save_json(report, run / "eval_report.json")
    with open(run / "errors.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["korean", "reference", "transformer", "baseline", "exact"])
        for r, h, b in zip(recs, hyp_model, hyp_base):
            if h != r["gloss"]:
                w.writerow([r["korean"], " ".join(r["gloss"]), " ".join(h), " ".join(b), 0])

    t, b = report["transformer"], report["dict_substitution_baseline"]
    print(f"Transformer : BLEU {t['bleu']:.2f} ({t['bleu_0to1']:.4f}) EM {t['exact_match']:.3f} F1 {t['gloss_f1']:.3f}")
    if hyp_nolex:
        nl = report["transformer_without_lexicon"]
        print(f"  (사전 미사용): BLEU {nl['bleu']:.2f} EM {nl['exact_match']:.3f}")
    print(f"사전 치환   : BLEU {b['bleu']:.2f} ({b['bleu_0to1']:.4f}) EM {b['exact_match']:.3f} F1 {b['gloss_f1']:.3f}")
    print(f"AI Hub 기준 : BLEU {AIHUB_BASELINE_BLEU} (0~1)")
    print(f"→ {run / 'eval_report.json'}, {run / 'errors.csv'}")


if __name__ == "__main__":
    main()
