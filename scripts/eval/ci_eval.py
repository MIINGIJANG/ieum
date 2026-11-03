"""배포 전 환각 방지 평가 (GitHub Actions): 기준 점수(data/eval/baseline.json)보다 떨어지면 실패 → 배포 안 함.

  python scripts/eval/ci_eval.py            # 결과 표는 $GITHUB_STEP_SUMMARY 에도 씀

- 번역 모델·Claude 없이 (규칙 기반 답변) 돌림: 비용 0원, 1~2분
- 안전 안내 문장은 번역 모델 대신 '번역으로 가는지(mode=translate)'만 확인
- 기준: 문서 밖 입력은 하나도 답하면 안 됨, 문서 안 질문 답함·정답 쪽 출처·안내 문장 번역은 기준 이상
  (점수를 일부러 바꿨으면 baseline.json 을 고쳐서 같이 올리기)
"""
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("KSL_MISSING_LOG", str(Path(tempfile.mkdtemp()) / "missing.json"))

from ksl.evaluation import evaluate  # noqa: E402
from ksl.pipeline import SafetySignPipeline  # noqa: E402
from ksl.rag import SafetyRAG  # noqa: E402


class _NoModel:
    """번역 모델 대신: 안내 문장이 번역으로 가는지만 보면 되므로 빈 결과."""
    def translate(self, text, beam=4):
        return {"korean": text, "gloss": [], "timeline": [], "lexicon_terms": [], "dropped_terms": [],
                "unverified_terms": [], "has_unk": False}


def main():
    idx = Path(tempfile.mkdtemp()) / "kosha_index.pkl"
    SafetyRAG.build([str(ROOT / "data/kosha/A-G-12-2026.pdf")], max_chars=300).save(idx)   # 서버와 같은 설정
    pipe = SafetySignPipeline("no-model.pt", str(idx), "data/sign/lexicon.csv", "data/sign/motions",
                              "data/sign/demo_sentences.json")
    pipe._tr = _NoModel()
    r = evaluate(pipe, detail=True)
    d = r["자세히"]
    inq, oos, sents = d["문서 안 질문"], d["문서 밖 입력"], d["안전 안내 문장"]
    gold = [x for x in inq if x["정답쪽"]]
    got = {
        "answered": sum(x["답함"] for x in inq),
        "hit": sum(x["답함"] and int(x["정답쪽"]) in x["출처쪽"] for x in gold),
        "leaked": sum(not x["답하지 않음"] for x in oos),
        "translated": sum(x["번역함"] for x in sents),
    }
    base = json.loads((ROOT / "data/eval/baseline.json").read_text(encoding="utf-8"))
    checks = [
        ("문서 안 질문 답함", got["answered"], f"≥ {base['answered']} / {len(inq)}", got["answered"] >= base["answered"]),
        ("정답 쪽이 출처에 있음", got["hit"], f"≥ {base['hit']} / {len(gold)}", got["hit"] >= base["hit"]),
        ("문서 밖 입력에 답함 (환각)", got["leaked"], f"≤ {base['leaked']} / {len(oos)}", got["leaked"] <= base["leaked"]),
        ("안전 안내 문장 번역", got["translated"], f"≥ {base['translated']} / {len(sents)}", got["translated"] >= base["translated"]),
    ]
    ok = all(c[3] for c in checks)
    lines = ["## 환각 방지 평가 (규칙 기반 답변)", "", "| 항목 | 결과 | 기준 | |", "|---|---|---|---|"]
    lines += [f"| {n} | {v} | {b} | {'✅' if p else '❌'} |" for n, v, b, p in checks]
    leaked = [x["입력"] for x in oos if not x["답하지 않음"]]
    missed = [x["질문"] for x in inq if not x["답함"]]
    if leaked:
        lines += ["", "**답하면 안 되는데 답한 입력:** " + ", ".join(leaked)]
    if missed:
        lines += ["", "**답하지 못한 문서 안 질문:** " + ", ".join(missed)]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if not ok:
        print("::error::환각 방지 평가가 기준보다 떨어졌습니다 (위 표). 의도한 변화면 data/eval/baseline.json 을 고치세요.")
    print(json.dumps(got))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
