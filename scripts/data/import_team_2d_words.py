"""팀원이 보낸 낱말 파일(2D 관절 점) → 동작사전.

python scripts/data/import_team_2d_words.py <폴더> --dry                 # 점검만
python scripts/data/import_team_2d_words.py <폴더> --only 마다 후 --replace

파일: <낱말>.json  {"gloss","fps","n_frames","source_id","landmarks": {pose_keypoints_3d, hand_*_keypoints_3d, face_keypoints_3d}}
  이름은 '_3d' 지만 값은 점마다 (픽셀 x, 픽셀 y, 신뢰도 0~1) 인 2D 자료 (2026-11-13 전달본) → 2D 로 읽어 깊이를 추정함
  (기존 3D 동작의 뼈 길이로, ksl/motion.py lift_2d). 세 번째 값이 1보다 크면 진짜 3D 로 보고 scripts/data/import_team_3d.py 를 쓰라고 알림.
점검 (하나라도 걸리면 가져오지 않음 → 사람이 확인):
  손이 잡힌 프레임 비율, 손이 든 구간의 길이, 팔 뼈 길이가 튀는 프레임, 손이 머리 위·옆으로 크게 벗어나는지
이미 있는 낱말은 --replace 를 줄 때만 바꿈. 검수에서 뺀 낱말(REMOVED)은 저장되지 않음.
"""
import argparse
import json
import math
import statistics
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict, bone_lengths, calibrate_expressions, lift_2d, load_team_clip, normalize, trim_rest  # noqa: E402

nfc = lambda s: unicodedata.normalize("NFC", s)


def read(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    lm = d["landmarks"]
    third = [v for k in lm for row in lm[k] for v in row[2::3]]
    if third and max(third) > 1.5:
        raise SystemExit(f"{path.name}: 세 번째 값이 신뢰도가 아님(진짜 3D) → scripts/data/import_team_3d.py 를 쓰세요")
    d["landmarks"] = {k.replace("_3d", "_2d"): v for k, v in lm.items()}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as t:
        json.dump(d, t)
    frames, fps, _ = load_team_clip(t.name)
    Path(t.name).unlink()
    return d, frames, fps


def check(raw, fr):
    """정규화한(어깨너비 단위, +Y 아래) 동작의 점검 → (통과 여부, 수치, 문제 목록)"""
    n = len(raw) or 1
    rh, lh = sum(f["rh"] is not None for f in raw) / n, sum(f["lh"] is not None for f in raw) / n
    probs = []
    if max(rh, lh) < 0.6:
        probs.append(f"손이 잘 안 잡힘 (오른손 {rh:.0%}, 왼손 {lh:.0%})")
    if len(fr) < 8:
        probs.append(f"손을 든 구간이 너무 짧음 ({len(fr)}프레임)")
    worst = 1.0
    for i, j in ((2, 3), (3, 4), (5, 6), (6, 7)):
        ls = [math.dist(f["body"][i][:2], f["body"][j][:2]) for f in fr if f.get("body") and f["body"][i] and f["body"][j]]
        if ls:
            worst = max(worst, max(ls) / (statistics.median(ls) or 1))
    wr = [f["body"][w] for f in fr if f.get("body") for w in (4, 7) if f["body"][w]]
    nose = statistics.median([f["body"][0][1] for f in fr if f.get("body") and f["body"][0]] or [-0.5])
    top = min((p[1] for p in wr), default=0) - nose          # 음수 = 코보다 위
    side = max((abs(p[0]) for p in wr), default=0)
    if top < -0.5:
        probs.append(f"손목이 코보다 {-top:.2f} 위로 올라감")
    if side > 1.5:
        probs.append(f"손목이 옆으로 {side:.2f} 벌어짐")
    return not probs, {"오른손": round(rh, 2), "왼손": round(lh, 2), "프레임": len(fr), "뼈 길이 튐": round(worst, 2),
                       "손목 높이(코 기준)": round(top, 2), "옆": round(side, 2)}, probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default="data/sign/motions")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--replace", action="store_true", help="이미 있는 낱말도 바꿈")
    ap.add_argument("--force", action="store_true", help="점검에 걸려도 가져옴")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    md = MotionDict(a.out)
    L = bone_lengths([md.get(g)["frames"] for g in md.index if "2D" not in (md.get(g).get("source") or "")])
    clips = {}
    for p in sorted(Path(a.folder).glob("*.json")):
        word = nfc(p.stem)
        if a.only and word not in a.only:
            continue
        clips[word] = read(p)
    calibrate_expressions([c[1] for c in clips.values()])
    for word, (d, raw, fps) in clips.items():
        fr = trim_rest(lift_2d(normalize(raw), L))
        ok, nums, probs = check(raw, fr)
        g = md.resolve(word + "1") or word + "1"
        have = md.resolve(word + "1") is not None
        act = "점검에 걸림 → 가져오지 않음" if not (ok or a.force) else "이미 있음 → 그대로" if have and not a.replace else "바꿈" if have else "추가"
        print(f"  {word:<5} → {g:<6} {act}  {nums}" + ("".join(f"\n        ⚠ {x}" for x in probs)))
        if a.dry or not (ok or a.force) or (have and not a.replace):
            continue
        MotionDict.save_clip(a.out, g, fr, round(fps),
                             source=f"팀원 낱말 자료(2026-11-13) AI Hub {d.get('source_id', '')} (2D→3D, {len(raw)}→{len(fr)}프레임)")


if __name__ == "__main__":
    main()
