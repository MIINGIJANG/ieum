"""환각 방지(답변 거절) 평가 — scripts/eval_abstain.py 와 서버 /eval 이 함께 씀.

- 문서 안 질문(dev 30 + heldout 10): 답하는 비율, 답한 것 중 정답 쪽이 출처에 있는 비율
- 문서 밖 입력 28개: 답하지 않는 비율
- 안전 안내 문장 10개: 번역(수어)하는 비율
"""
import csv
from collections import Counter
from pathlib import Path

K = Path(__file__).resolve().parent.parent / "data" / "kosha"


def _rows(name):
    with open(K / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def evaluate(p, detail=False):
    inq = _rows("eval_questions.csv") + _rows("heldout_questions.csv")
    res = {"문서 안 질문": [], "문서 밖 입력": [], "안전 안내 문장": []}
    answered = hit = 0
    modes = Counter()
    for r in inq:
        out = p.query(r["question"])
        ok = out["mode"] != "none"
        modes[out.get("answer_mode") or out["mode"]] += 1
        good = bool(r["gold_page"]) and any(s["page"] == int(r["gold_page"]) for s in out.get("sources", []))
        answered += ok
        hit += ok and good
        res["문서 안 질문"].append({"질문": r["question"], "답함": ok, "방식": out.get("answer_mode"),
                                "정답쪽": r["gold_page"], "출처쪽": [s["page"] for s in out.get("sources", [])],
                                "답변": out.get("answer_text", "")})
    gold = sum(1 for r in inq if r["gold_page"])
    oos = _rows("out_of_scope_questions.csv")
    refused = 0
    for r in oos:
        out = p.query(r["question"])
        refused += out["mode"] == "none"
        res["문서 밖 입력"].append({"입력": r["question"], "답하지 않음": out["mode"] == "none",
                                "답변": out.get("answer_text", "")})
    sents = _rows("safety_sentences.csv")
    translated = 0
    for r in sents:
        out = p.query(r["sentence"])
        translated += out["mode"] == "translate"
        res["안전 안내 문장"].append({"문장": r["sentence"], "번역함": out["mode"] == "translate"})
    summary = {
        "문서 안 질문": f"{len(inq)}개 중 답함 {answered} ({answered/len(inq):.0%}), 정답 쪽이 출처에 있음 {hit}/{gold}",
        "답변 방식": dict(modes),
        "문서 밖 입력": f"{len(oos)}개 중 답하지 않음 {refused} ({refused/len(oos):.0%})",
        "새어 나간 입력": [x["입력"] for x in res["문서 밖 입력"] if not x["답하지 않음"]],
        "안전 안내 문장": f"{len(sents)}개 중 번역함 {translated}",
        "답하지 못한 문서 안 질문": [x["질문"] for x in res["문서 안 질문"] if not x["답함"]],
    }
    return {"요약": summary, **({"자세히": res} if detail else {})}
