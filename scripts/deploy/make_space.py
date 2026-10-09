"""Hugging Face Space 에 올릴 폴더 만들기.

  python scripts/deploy/make_space.py [번역모델.pt]      # 기본: checkpoints/test/best.pt
  python scripts/deploy/make_space.py --no-ckpt         # 모델 파일 빼고 (GitHub 자동 배포용: Space 에 이미 올린 모델을 그대로 씀)
  → dist/space/  (서버 실행에 필요한 파일만. 원본 데이터·한글 파일명·문서 제외)

그다음:  hf upload <아이디>/ieum dist/space . --repo-type space
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dist" / "space"
KEEP = ["api.py", "requirements.txt", "ksl", "web", "data/kosha", "data/sign", "data/eval"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "avatar_female.vrm")


def main():
    no_ckpt = "--no-ckpt" in sys.argv
    ckpt = Path(sys.argv[1] if len(sys.argv) > 1 and not no_ckpt else ROOT / "checkpoints" / "test" / "best.pt")
    if not no_ckpt and not ckpt.exists():
        sys.exit(f"번역 모델 파일이 없습니다: {ckpt}\n  python scripts/deploy/make_space.py <best.pt 경로>")
    if OUT.exists():
        shutil.rmtree(OUT)
    for k in KEEP:
        src, dst = ROOT / k, OUT / k
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree(src, dst, ignore=SKIP) if src.is_dir() else shutil.copy2(src, dst))
    if not no_ckpt:
        (OUT / "checkpoints").mkdir()
        shutil.copy2(ckpt, OUT / "checkpoints" / "best.pt")
    idx = ROOT / "checkpoints" / "kosha_index.pkl"
    if idx.exists() and not no_ckpt:
        shutil.copy2(idx, OUT / "checkpoints" / "kosha_index.pkl")
    for f in ("Dockerfile", "README.md"):
        shutil.copy2(ROOT / "deploy" / "huggingface" / f, OUT / f)
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1e6
    print(f"완료: {OUT}  ({size:.0f} MB)")
    print("다음: hf upload <아이디>/ieum dist/space . --repo-type space")


if __name__ == "__main__":
    main()
