"""평가 지표.

- BLEU: 글로스 단위로 이미 토큰화돼 있으므로 sacrebleu tokenize='none'.
  sacrebleu 는 0~100 스케일 → AI Hub 기준값(BLEU 0.1633)과 비교할 때는 /100 한 값을 본다.
- 정확 일치율, 글로스 단위 F1(순서 무시) — BLEU가 낮을 때 '어휘는 맞는데 어순이 틀린지' 진단용.
"""
from collections import Counter

import sacrebleu


def corpus_bleu(hyps, refs):
    h = [" ".join(x) for x in hyps]
    r = [" ".join(x) for x in refs]
    b = sacrebleu.corpus_bleu(h, [r], tokenize="none")
    return {"bleu": round(b.score, 2), "bleu_0to1": round(b.score / 100, 4),
            "bleu_1to4_precisions": [round(p, 1) for p in b.precisions]}


def exact_match(hyps, refs):
    return round(sum(h == r for h, r in zip(hyps, refs)) / max(len(refs), 1), 4)


def gloss_f1(hyps, refs):
    tp = fp = fn = 0
    for h, r in zip(hyps, refs):
        ch, cr = Counter(h), Counter(r)
        inter = sum((ch & cr).values())
        tp += inter
        fp += sum(ch.values()) - inter
        fn += sum(cr.values()) - inter
    p = tp / max(tp + fp, 1)
    rc = tp / max(tp + fn, 1)
    return round(2 * p * rc / max(p + rc, 1e-9), 4)


def all_metrics(hyps, refs):
    m = corpus_bleu(hyps, refs)
    m["exact_match"] = exact_match(hyps, refs)
    m["gloss_f1"] = gloss_f1(hyps, refs)
    m["n"] = len(refs)
    return m
