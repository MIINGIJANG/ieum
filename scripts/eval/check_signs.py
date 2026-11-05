"""수어 단어 점검 (팀원 SignBridge 의 suspect_align.py · check_silent_skip.mjs 를 우리 구조에 맞게 옮김).

  python scripts/eval/check_signs.py            # 보고만 (실패로 치지 않음)
  python scripts/eval/check_signs.py --strict   # 대체 표현이 가리키는 글로스에 동작이 없으면 실패

1) 대체 표현이 가리키는데 동작이 없는 글로스 → 대체가 조용히 안 됨 (--strict 면 실패)
   단어표(sign_words.csv)가 가리키는데 동작이 없는 글로스 → 회색 블록 (대체 표현이 있는 것·검수에서 뺀 것은 따로 표시)
2) 의심 대응: 말과 글로스가 글자를 하나도 같이 쓰지 않는 단어표 줄 (예: 작업자 → 사람1).
   전부 틀린 건 아니지만(수어는 다른 낱말을 쓰기도 함) 잘못된 수어는 표현 안 된 것보다 나쁘므로 사람이 한 번 볼 목록
3) 대체 표현 목록 (검수 전): substitutes.csv 전체
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ksl.motion import REMOVED, MotionDict  # noqa: E402

HANGUL = re.compile(r"[가-힣]")


def base(g):
    return re.sub(r"[\d#]+$", "", g)


def rows(path):
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#") or ln.startswith("말,"):
            continue
        out.append([c.strip() for c in ln.split(",")])
    return out


def report():
    md = MotionDict(ROOT / "data/sign/motions")
    words = [(r[0].lstrip("="), r[-1]) for r in rows(ROOT / "data/sign/sign_words.csv") if len(r) >= 2]
    subs = [(r[0], r[1].split(), r[2] if len(r) > 2 else "") for r in rows(ROOT / "data/sign/substitutes.csv") if len(r) >= 2]
    sub_src = {w for w, gs, _ in subs if all(md.get(g) for g in gs)}
    broken = sorted({g for _, gs, _ in subs for g in gs if not md.get(g)})
    gray = sorted({g for _, g in words if not md.get(g) and g not in sub_src and g not in REMOVED})
    removed = sorted({g for _, g in words if g in REMOVED})
    suspect = [(w, g) for w, g in words if not (set(HANGUL.findall(w)) & set(HANGUL.findall(base(g))))]
    return {"broken": broken, "gray": gray, "removed": removed, "suspect": suspect, "subs": subs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    r = report()
    print(f"## 대체 표현이 가리키는데 동작 없음 (대체가 조용히 안 됨): {len(r['broken'])}개")
    print("  " + (", ".join(r["broken"]) or "없음"))
    print(f"\n## 단어표가 가리키는데 동작 없음 (회색 블록, 동작 추가 후보): {len(r['gray'])}개")
    print("  " + (", ".join(r["gray"]) or "없음"))
    print(f"  (검수에서 뺀 글로스: {', '.join(r['removed']) or '없음'})")
    print(f"\n## 의심 대응 (말과 글로스가 글자를 같이 쓰지 않음, 사람이 확인): {len(r['suspect'])}개")
    for w, g in r["suspect"]:
        print(f"  {w} → {g}")
    print(f"\n## 대체 표현 (검수 전): {len(r['subs'])}개")
    for w, gs, memo in r["subs"]:
        print(f"  {w} → {' '.join(gs)}  {memo}")
    return 1 if a.strict and r["broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
