"""KOSHA 문서로 RAG 인덱스 생성.

python scripts/data/build_index.py --docs data/kosha/A-G-12-2026.pdf --out checkpoints/kosha_index.pkl
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.rag import SafetyRAG  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--docs", nargs="+", required=True, help="PDF 또는 txt 여러 개 가능")
ap.add_argument("--out", default="checkpoints/kosha_index.pkl")
ap.add_argument("--retriever", choices=["tfidf", "dense"], default="tfidf")
ap.add_argument("--dense_model", default=None)
ap.add_argument("--max_chars", type=int, default=350)
a = ap.parse_args()

rag = SafetyRAG.build(a.docs, a.retriever, a.dense_model, a.max_chars)
rag.save(a.out)
print(f"chunk {len(rag.chunks)}개, 검색기={a.retriever} → {a.out}")
print("예시 chunk:", rag.chunks[0]["text"][:150] if rag.chunks else "(없음)")
