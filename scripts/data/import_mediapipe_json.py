"""MediaPipe 후처리 좌표 JSON(팀원 postprocess.py 결과, 국립국어원 수어사전 영상 등) → 동작사전.

python scripts/data/import_mediapipe_json.py ~/Downloads/보안경_14431_avatar.json:보안경1 ~/Downloads/작업장_272_avatar.json:작업장1

- 파일:글로스 로 적음 (글로스를 빼면 파일 이름 앞부분 + '1')
- 좌표 변환·깊이 배율 맞추기는 ksl/motion.py load_mediapipe_avatar
- 이미 있는 글로스는 덮어씀 (--keep 이면 그대로 둠)
"""
import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict, bone_lengths, load_mediapipe_avatar, normalize  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+", help="좌표.json[:글로스]")
ap.add_argument("--out", default="data/sign/motions")
ap.add_argument("--keep", action="store_true", help="이미 있는 글로스는 그대로 두기")
a = ap.parse_args()

md = MotionDict(a.out)
L = bone_lengths([md.get(g)["frames"] for g in md.index if "2D" not in (md.get(g).get("source") or "")])
for spec in a.files:
    path, _, gloss = spec.partition(":")
    name = unicodedata.normalize("NFC", Path(path).stem)
    gloss = gloss or name.split("_")[0] + "1"
    if a.keep and gloss in md.index:
        print(f"  {gloss}: 이미 있음 → 그대로")
        continue
    frames, fps, dim = load_mediapipe_avatar(path, L)
    MotionDict.save_clip(a.out, gloss, normalize(frames), round(fps), source=f"MediaPipe {name} (3D)")
    print(f"  {gloss:<8} {len(frames):>3}프레임 ← {name}")
