"""팀원 SignBridge 저장소(github.com/BioCode67/signbridge)의 낱말 동작 사전 → 이 저장소 동작사전에 골라서 추가.

python scripts/data/import_signbridge.py ~/signbridge/public/data              # 아래 PICK 전부
python scripts/data/import_signbridge.py ~/signbridge/public/data --only 종류0  # 일부만
python scripts/data/import_signbridge.py ~/signbridge/public/data --dry         # 저장하지 않고 보고만

- 입력: public/data/bank.json (글로스 → glosses/<파일>) 과 glosses/*.json
  {"keypoints": {"pose": [[x,y,신뢰도 ×25]...], "hand_left": [[×21]...], "hand_right": [[×21]...]}, "fps"}  픽셀 2D (AI Hub OpenPose)
- 몸은 2D 뿐이라 기존 3D 동작의 뼈 길이로 깊이를 추정해 3D 로 (ksl/motion.py lift_2d, import_aihub_clips.py 와 같은 방식)
- 숫자 수어(영·일·이 …)는 일반 낱말과 겹치지 않게 '수:2' 꼴 이름으로 저장 (ksl/pipeline.py number_glosses 가 씀)
- 이미 있는 글로스(번호만 다른 것 포함)와 검수에서 뺀 글로스(REMOVED)는 건드리지 않음
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import REMOVED, MotionDict, _strip_num, bone_lengths, lift_2d, load_team_clip, normalize, trim_rest  # noqa: E402

DIGITS = {"영": "0", "일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9",
          "십": "십", "백": "백", "천": "천", "만": "만"}
# 가져올 글로스 (SignBridge 이름 → 이 저장소에 저장할 이름). 우리 답변에 나오는데 동작이 없던 말 + 숫자.
# 같은 말에 번호가 여러 개면 SignBridge 사전(align.json)의 첫 후보를 고름
PICK = {"종류0": "종류0", "고려": "고려", "영향0": "영향0", "미터0": "미터0", "이하0": "이하0",
        "안전1": "안전1", "피부0": "피부0", "공간0": "공간0", "자전거0": "자전거0", "동일": "동일", "이유0": "이유0",
        "정기0": "정기0",
        **{k: f"수:{v}" for k, v in DIGITS.items()}}
# 아바타로 재생해 보고 뺀 것 (2D 에서 깊이를 추정한 탓인지 손이 머리 위로 올라가거나 두 팔을 크게 벌림)
SKIP = {"물체2": "팔이 머리 위로 올라가며 엇갈림", "평가": "처음에 손이 머리 위", "환자": "끝에서 두 손이 머리 위",
        "렌즈0": "두 손을 머리 높이로 벌림", "얼굴": "손이 머리 위로", "탱크": "손이 머리 위로", "기존0": "손이 머리 위로",
        "조사0": "두 손을 머리 높이로 벌림", "폭염0": "두 손이 머리 위로"}


def load(folder, name, entry):
    d = json.loads((Path(folder) / "glosses" / entry["file"]).read_text(encoding="utf-8"))
    kp = d["keypoints"]
    tmp = Path(tempfile.gettempdir()) / "_signbridge_clip.json"
    tmp.write_text(json.dumps({"gloss": name, "fps": d.get("fps", 30), "landmarks": {
        "pose_keypoints_2d": kp["pose"], "hand_right_keypoints_2d": kp.get("hand_right") or [],
        "hand_left_keypoints_2d": kp.get("hand_left") or []}}), encoding="utf-8")
    frames, fps, dim = load_team_clip(tmp)
    return frames, fps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="SignBridge 의 public/data")
    ap.add_argument("--out", default="data/sign/motions")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    bank = json.loads((Path(a.folder) / "bank.json").read_text(encoding="utf-8"))
    md = MotionDict(a.out)
    L = bone_lengths([md.get(g)["frames"] for g in md.index if "(2D)" not in (md.get(g).get("source") or "")])
    removed = {_strip_num(g) for g in REMOVED}
    n = 0
    for src, dst in PICK.items():
        if a.only and src not in a.only and dst not in a.only:
            continue
        if src not in bank:
            print(f"  {src:<6} SignBridge 사전에 없음")
            continue
        if _strip_num(dst) in removed or (not dst.startswith("수:") and md.resolve(dst)) or dst in md.index:
            print(f"  {src:<6} 이미 있거나 검수에서 뺀 글로스 → 그대로")
            continue
        frames, fps = load(a.folder, src, bank[src])
        fr = trim_rest(lift_2d(normalize(frames), L))
        print(f"  {src:<6} → {dst:<6} {len(fr):>3}프레임 ({len(fr) / fps:.1f}초)")
        if not a.dry:
            MotionDict.save_clip(a.out, dst, fr, round(fps), source=f"SignBridge 동작 사전 {src} (AI Hub, 2D→3D)")
        n += 1
    print(f"새 동작 {n}개" + (" (저장 안 함)" if a.dry else f" → {a.out}"))


if __name__ == "__main__":
    main()
