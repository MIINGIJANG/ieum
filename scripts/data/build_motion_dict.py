"""AI Hub 키포인트(OpenPose JSON) + 글로스 시간 → 글로스별 동작사전.

python scripts/data/build_motion_dict.py --data data/processed/ksl_safety.jsonl \
    --kp_root /path/to/aihub/keypoints --out data/sign/motions

동작 원리
  1) JSONL 각 문장의 글로스별 start/end(초)와 영상 id 를 읽음
  2) kp_root 아래에서 영상 id 가 이름에 들어간 키포인트 폴더/파일을 찾음
  3) 해당 구간 프레임을 잘라 정규화 → 같은 글로스 클립이 여러 개면 '길이가 중앙값에 가장 가까운 것' 1개 저장

영상 id 는 JSONL 의 --video_key 필드(기본: source)에서 가져옴. 경로/확장자가 붙어 있어도 파일명만 사용.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.data import load_jsonl  # noqa: E402
from ksl.motion import FPS, MotionDict, load_openpose, normalize  # noqa: E402


def find_kp(kp_root, vid, cache={}):
    if not cache:
        for p in Path(kp_root).rglob("*"):
            if p.is_dir() and any(p.glob("*.json")):
                cache.setdefault(p.name, p)
            elif p.suffix == ".json" and not re.search(r"_\d{6,}_keypoints\.json$", p.name):
                cache.setdefault(p.stem, p)
    if vid in cache:
        return cache[vid]
    for name, p in cache.items():
        if vid in name:
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--kp_root", required=True)
    ap.add_argument("--out", default="data/sign/motions")
    ap.add_argument("--video_key", default="source", help="JSONL 에서 영상 id 가 들어있는 필드")
    ap.add_argument("--fps", type=float, default=FPS, help="키포인트 프레임레이트")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    raw = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    vid_of = {}
    for r in raw:   # load_jsonl 은 정제만 하므로 원본에서 영상 id 매핑
        v = r.get(a.video_key)
        if v:
            vid_of[str(r.get("korean", ""))] = Path(str(v)).stem
    recs, _ = load_jsonl(a.data)

    clips = defaultdict(list)
    no_time = no_kp = 0
    frames_cache = {}
    for r in recs[: a.limit or None]:
        if not r["gloss_times"]:
            no_time += 1
            continue
        vid = vid_of.get(r["korean"]) or Path(str(r.get("source") or "")).stem
        kp = find_kp(a.kp_root, vid) if vid else None
        if not kp:
            no_kp += 1
            continue
        if kp not in frames_cache:
            frames_cache = {kp: load_openpose(kp)}   # 영상 1개씩만 메모리에 유지
        frames = frames_cache[kp]
        for g, (s, e) in zip(r["gloss"], r["gloss_times"]):
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)) or e <= s:
                continue
            seg = frames[int(s * a.fps): int(e * a.fps) + 1]
            if len(seg) >= 3:
                clips[g].append((len(seg), seg, f"{vid}@{s:.2f}-{e:.2f}"))

    saved = 0
    for g, cs in clips.items():
        med = sorted(c[0] for c in cs)[len(cs) // 2]
        n, seg, src = min(cs, key=lambda c: abs(c[0] - med))
        MotionDict.save_clip(a.out, g, normalize(seg), round(a.fps), source=src)
        saved += 1
    print(f"글로스 {saved}개 동작 저장 → {a.out}")
    print(f"건너뜀: 글로스 시간 없음 {no_time}문장, 키포인트 못 찾음 {no_kp}문장")


if __name__ == "__main__":
    main()
