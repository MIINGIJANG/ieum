"""동작 이어붙이기 (ksl/motion.py compose): 단어 사이 연결 구간 표시·멈춤·떨림 필터, 손 모양 군집 파일."""
import json
import math
import random

from ksl.motion import MotionDict, _despike, _hand_basis, _one_euro, _smooth_hand_rot, compose

from conftest import ROOT

md = MotionDict(ROOT / "data/sign/motions")


def test_transition_marks():
    m = compose(["위험1", "조심1", "입다1"], md)
    tr = [(i, f["tr"]) for i, f in enumerate(m["frames"]) if "tr" in f]
    assert tr, "단어 사이 연결 프레임이 없음"
    for i, t in tr:
        assert t["a"] < i < t["b"] and 0 < t["t"] < 1
        assert "tr" not in m["frames"][t["a"]] and "tr" not in m["frames"][t["b"]]   # 양 끝은 실제 동작 프레임
    seg = m["segments"]
    assert all(s["has_motion"] for s in seg) and seg[0]["end"] < seg[1]["start"]
    assert m["frames"][seg[0]["end"]] == m["frames"][seg[0]["end"] + 1]             # 단어 끝 멈춤


def test_longer_transition_for_longer_move():
    """손이 멀리 움직일수록 연결이 길어짐."""
    a = {"body": [[0, -0.5, 0]] + [[0, 0, 0]] * 3 + [[0.0, 0.5, 0]] + [[0, 0, 0]] * 2 + [[0.5, 1.0, 0]]}
    near = dict(a, body=[p[:] for p in a["body"]]); near["body"][4] = [0.1, 0.5, 0]
    far = dict(a, body=[p[:] for p in a["body"]]); far["body"][4] = [0.0, -0.8, 0]

    class D:
        def __init__(self, clips): self.clips = clips
        def get(self, g): return self.clips.get(g)
    clip = lambda f: {"fps": 30, "frames": [f] * 6}
    n = lambda b: sum("tr" in f for f in compose(["x", "y"], D({"x": clip(a), "y": clip(b)}))["frames"])
    assert n(far) > n(near)


def test_one_euro_reduces_jitter_keeps_motion():
    random.seed(0)
    frames, true = [], []
    for i in range(60):
        x = 0.0 if i < 30 else 1.0                                  # 가만히 있다가 한 번 크게 움직임
        true.append(x)
        frames.append({"body": [[x + random.gauss(0, 0.02), 0.5]] * 8})
    out = _one_euro(frames)
    jit = lambda fs: math.sqrt(sum((f["body"][0][0] - t) ** 2 for f, t in zip(fs[5:28], true[5:28])) / 23)
    assert jit(out) < jit(frames) * 0.7                             # 가만히 있을 때 떨림 줄어듦
    assert abs(out[45]["body"][0][0] - 1.0) < 0.05                  # 움직인 뒤엔 금방 따라감


def test_hand_flip_removed():
    """손바닥 앞뒤가 한 프레임만 뒤집힌 추정 → 손 방향이 튀지 않게 (손가락 모양·손목 위치는 그대로)."""
    hand = [[0, 0, 0]] + [[0.02 * (i % 4), -0.02 * (i // 4 + 1), 0.01 * (i % 3)] for i in range(20)]
    hand[5], hand[9], hand[17] = [-0.03, -0.08, 0], [0, -0.09, 0], [0.04, -0.07, 0]
    flip = [[p[0], p[1], -p[2]] if i else p for i, p in enumerate(hand)]
    flip[17] = [-0.04, -0.07, 0.0]; flip[5] = [0.03, -0.08, 0.0]       # 손바닥이 반대로
    frames = [{"rh": hand} for _ in range(5)] + [{"rh": flip}] + [{"rh": hand} for _ in range(5)]
    out = _smooth_hand_rot(frames)
    ang = lambda A, B: math.degrees(math.acos(max(-1, min(1, ((A.T @ B).trace() - 1) / 2))))
    B = [_hand_basis(f["rh"]) for f in out]
    assert max(ang(B[i], B[i + 1]) for i in range(len(B) - 1)) < 20
    assert out[5]["rh"][0] == hand[0]


def test_despike_depth_jump():
    """2D 영상에서 깊이를 추정한 자료: 한 프레임만 앞뒤 깊이가 튐 → 앞뒤 프레임 쪽으로 (천천히 움직이는 건 그대로)."""
    body = lambda z: [[0, 0, 0]] * 4 + [[-0.2, 0.1, z]] + [[0, 0, 0]] * 3
    zs = [-0.4, -0.42, -0.44, -0.1, -0.46, -0.48, -0.5]
    out = _despike([{"body": body(z)} for z in zs])
    assert abs(out[3]["body"][4][2] - (-0.44)) < 0.03
    assert [f["body"][4][2] for i, f in enumerate(out) if i != 3] == [z for i, z in enumerate(zs) if i != 3]


def test_handshapes_file():
    d = json.loads((ROOT / "web/handshapes.json").read_text(encoding="utf-8"))
    assert len(d["centers"]) >= 8 and all(len(c) == 15 for c in d["centers"])
    assert 5 < d["scale"] < 40
