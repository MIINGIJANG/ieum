"""RAG 검색 성능: Recall@1/3/5 (목표: Recall@3 ≥ 0.90).

python scripts/eval_retrieval.py --index checkpoints/kosha_index.pkl --questions data/rag_questions.csv
질문 CSV 열: question, gold_page, gold_keyword  (gold_keyword 는 '|' 로 여러 개 AND 조건)
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksl.rag import SafetyRAG, is_hit, load_questions  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--index", required=True)
ap.add_argument("--questions", required=True)
ap.add_argument("--out", default=None, help="질문별 결과 CSV 저장 경로")
a = ap.parse_args()

rag = SafetyRAG.load(a.index)
qs = load_questions(a.questions)
ks = (1, 3, 5)
hits = {k: 0 for k in ks}
rows = []
for q in qs:
    res = rag.search(q["question"], max(ks))
    rank = next((i + 1 for i, c in enumerate(res) if is_hit(c, q)), None)
    for k in ks:
        hits[k] += bool(rank and rank <= k)
    rows.append([q["question"], rank or "-", res[0]["page"], res[0]["text"][:80]])

n = len(qs)
for k in ks:
    flag = "  ← 목표 0.90" if k == 3 else ""
    print(f"Recall@{k}: {hits[k] / n:.3f} ({hits[k]}/{n}){flag}")
miss = [r for r in rows if r[1] == "-" or r[1] > 3]
if miss:
    print("\n상위 3개 안에 정답 없는 질문:")
    for r in miss:
        print(f"  - {r[0]}  (1위 p.{r[2]}: {r[3]}...)")
if a.out:
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "gold_rank", "top1_page", "top1_text"])
        w.writerows(rows)
