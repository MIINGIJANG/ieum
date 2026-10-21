"""답변 거절 기준값(min_score) 찾기: 값을 바꿔 가며 문서 안 질문 답함 + 문서 밖 입력 거절 수를 셈.

python scripts/eval/sweep_threshold.py

- 문서 안 질문 40개(dev 30 + heldout 10)는 답해야 하고, 문서 밖 입력 28개는 답하지 않아야 함
- 같은 질문으로 기준값을 고르는 것이라 새 질문에서는 성능이 낮을 수 있음 (새 질문 세트로 다시 확인할 것)
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ksl.evaluation import _rows  # noqa: E402
from ksl.pipeline import SafetySignPipeline  # noqa: E402

p = SafetySignPipeline("unused", "checkpoints/kosha_index.pkl", "data/sign/lexicon.csv", "data/sign/motions",
                       "data/sign/demo_sentences.json")
inq = [r["question"] for r in _rows("eval_questions.csv") + _rows("heldout_questions.csv")]
oos = [r["question"] for r in _rows("out_of_scope_questions.csv")]
print("기준값  문서 안 답함  문서 밖 거절  합")
for t in [0.0, 0.05, 0.08, 0.09, 0.10, 0.105, 0.11, 0.115, 0.12, 0.13, 0.15, 0.18]:
    p.min_score = t
    a = sum(p.query(q)["mode"] != "none" for q in inq)
    r = sum(p.query(q)["mode"] == "none" for q in oos)
    print(f"{t:<6}  {a}/{len(inq)}         {r}/{len(oos)}         {a + r}")
