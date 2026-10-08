"""웹/아바타 연결 테스트용 '가짜' 동작사전 (실제 수어 동작 아님!).

샘플 글로스마다 손이 서로 다른 궤적으로 움직이는 클립을 만들어 data/sample/motions 에 저장.
실제 동작은 build_motion_dict.py(AI Hub) / extract_keypoints.py(사전 영상)로 만든다.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksl.motion import FPS, MotionDict, neutral_frame  # noqa: E402

OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "data/sample/motions")
DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample/sample_ksl.jsonl")


def hand_at(wx, wy, spread, curl, flip):
    """손목 위치에 21점 손 모양 생성. spread=손가락 벌림, curl=굽힘(0~1)."""
    pts = [[wx, wy]]
    base_angles = [-1.9, -1.75, -1.57, -1.4, -1.25]  # 엄지~소지 (위쪽 방향)
    for f, ang in enumerate(base_angles):
        a = ang + (ang + 1.57) * spread
        a = math.pi - a if flip else a
        length = [0.09, 0.11, 0.12, 0.11, 0.09][f]
        x, y = wx + math.cos(a) * 0.08, wy + math.sin(a) * 0.08
        for j in range(4):
            bend = curl * (j + 1) * 0.35 * (-1 if flip else 1)
            x += math.cos(a + bend) * length * 0.5
            y += math.sin(a + bend) * length * 0.5
            pts.append([round(x, 4), round(y, 4)])
    return pts


def elbow_ik(sh, wr, side, a=0.72, b=0.68):
    """어깨·손목 위치에서 팔꿈치 위치 계산 (2관절 IK, 팔꿈치는 바깥·아래쪽으로 굽힘)."""
    dx, dy = wr[0] - sh[0], wr[1] - sh[1]
    d = max(1e-6, min(math.hypot(dx, dy), a + b - 1e-3))
    ang = math.atan2(dy, dx)
    cos_a = (a * a + d * d - b * b) / (2 * a * d)
    off = math.acos(max(-1, min(1, cos_a)))
    c1 = (sh[0] + a * math.cos(ang + off), sh[1] + a * math.sin(ang + off))
    c2 = (sh[0] + a * math.cos(ang - off), sh[1] + a * math.sin(ang - off))
    out = lambda c: c[1] * 1.0 + abs(c[0]) * 0.6        # 아래(+y)·바깥쪽일수록 선호
    e = max((c1, c2), key=out)
    return [round(e[0], 4), round(e[1], 4)]


def clip_for(gloss):
    h = int(hashlib.md5(gloss.encode()).hexdigest(), 16)
    n = 18 + h % 14
    cx, cy = -0.35 + (h % 7) * 0.1, 0.05 + (h >> 3) % 5 * 0.12
    rx, ry = 0.08 + (h >> 6) % 4 * 0.04, 0.05 + (h >> 9) % 3 * 0.04
    two = (h >> 12) % 2 == 0
    frames = []
    for i in range(n):
        t = i / (n - 1) * 2 * math.pi
        rw = [round(cx + rx * math.cos(t), 4), round(cy + ry * math.sin(t), 4)]
        lw = [round(-cx * 0.8 + 0.1 - rx * math.cos(t), 4), round(cy + ry * math.sin(t), 4)] if two else [0.55, 1.45]
        body = neutral_frame()["body"]
        body[4], body[7] = rw, lw
        body[3] = elbow_ik(body[2], rw, "r")
        body[6] = elbow_ik(body[5], lw, "l")
        curl = 0.5 + 0.5 * math.sin(t + (h % 5))
        frames.append({"body": body,
                       "rh": hand_at(*rw, spread=0.4, curl=curl, flip=False),
                       "lh": hand_at(*lw, spread=0.3, curl=1 - curl, flip=True) if two else None})
    return frames


glosses = sorted({g["gloss"] if isinstance(g, dict) else g
                  for l in open(DATA, encoding="utf-8") if l.strip()
                  for g in (json.loads(l).get("gloss") or [])} - {""})
for g in glosses:
    MotionDict.save_clip(OUT, g, clip_for(g), FPS, source="SAMPLE(가짜 동작)")
print(f"{len(glosses)}개 가짜 동작 → {OUT}")
