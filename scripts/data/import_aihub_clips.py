"""AI Hub 형태소 JSON 에서 잘라낸 단어 구간(조원 형식 2) → 동작사전에 추가.

python scripts/data/import_aihub_clips.py data/raw/aihub_sel --gloss 보호1 사람1 ... --out data/sign/motions

- 입력: <글로스>__<n>.json.gz (같은 글로스 후보 여러 개) — 맥에서 형태소 VL 을 훑어 뽑은 것
- 후보 중 3D 이면서 손이 가장 잘 잡힌 것 1개를 고름 (2D 뿐이면 기존 3D 동작의 뼈 길이로 깊이를 추정해 3D 로)
- 표정은 후보 전체 얼굴 분포로 보정
"""
import argparse
import gzip
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict, bone_lengths, calibrate_expressions, lift_2d, load_team_clip, normalize  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("--gloss", nargs="*", default=None, help="이 글로스만 (없으면 폴더 전체)")
ap.add_argument("--out", default="data/sign/motions")
ap.add_argument("--dry", action="store_true")
ap.add_argument("--skip_existing", action="store_true", help="이미 있는 글로스(조원 자료)는 그대로 두기")
a = ap.parse_args()
# 검수에서 빼기로 한 글로스 (잘라 온 동작이 너무 부자연스러움) — 다시 가져오지 않음
EXCLUDE = {"물건1"}


def load(p):
    d = json.loads(gzip.open(p, "rt", encoding="utf-8").read())
    tmp = Path("/tmp/_aihub_clip.json")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    frames, fps, dim = load_team_clip(tmp)
    return d, frames, fps, dim


def score(d, frames):
    """손 검출 비율 (주로 쓰는 손 가중) - 너무 짧거나 긴 구간은 감점"""
    n = len(frames) or 1
    rh = sum(f["rh"] is not None for f in frames) / n
    lh = sum(f["lh"] is not None for f in frames) / n
    body = sum(f["body"] is not None for f in frames) / n
    dur = d["end"] - d["start"]
    pen = 0.0 if 0.3 <= dur <= 2.5 else 0.3
    both = d.get("kind", "").endswith("both")
    hand = (rh + lh) / 2 if both else max(rh, lh)
    return round(hand * 0.7 + body * 0.3 - pen - outlier(frames), 3)


def outlier(frames):
    """팔 뼈 길이가 클립 중앙값의 1.4배를 넘는 프레임이 있으면 (키포인트가 튄 것) 큰 감점"""
    import math
    import statistics
    fr = normalize(frames)
    worst = 1.0
    for i, j in ((2, 3), (3, 4), (5, 6), (6, 7)):
        ls = [math.dist(f["body"][i], f["body"][j]) for f in fr if f.get("body") and f["body"][i] and f["body"][j]]
        if ls:
            worst = max(worst, max(ls) / (statistics.median(ls) or 1))
    return 2.0 if worst > 1.4 else 0.0


cands = {}
for p in sorted(Path(a.root).glob("*.json.gz")):
    g = unicodedata.normalize("NFC", re.sub(r"__\d+\.json\.gz$", "", p.name))
    if a.gloss and g not in a.gloss:
        continue
    d, frames, fps, dim = load(p)
    cands.setdefault(g, []).append((score(d, frames) + (0.5 if dim == 3 else 0), p.name, d, frames, fps, dim))

# 표정 보정: 후보 전체 분포
calibrate_expressions([c[3] for cs in cands.values() for c in cs])

md = MotionDict(a.out)
L = bone_lengths([md.get(g)["frames"] for g in md.index if "2D" not in (md.get(g).get("source") or "")])
for g, cs in sorted(cands.items()):
    cs.sort(key=lambda c: -c[0])
    s, name, d, frames, fps, dim = cs[0]
    print(f"  {g:<8} {dim}D 점수 {s:.2f}  {len(frames):>3}프레임  ← {d['source']} {d['start']:.2f}-{d['end']:.2f}s  "
          f"({d.get('korean_text', '')[:30]})")
    if a.dry or s < 0.5 or g in EXCLUDE or (a.skip_existing and g in md.index):
        continue
    fr = normalize(frames)
    if dim == 2:
        fr = lift_2d(fr, L)
    MotionDict.save_clip(a.out, g, fr, round(fps),
                         source=f"AI Hub VL {d['source']} {d['start']:.2f}-{d['end']:.2f}s ({'2D→3D' if dim == 2 else '3D'})")
