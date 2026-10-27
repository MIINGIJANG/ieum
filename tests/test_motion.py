"""동작 이어붙이기 (ksl/motion.py compose): 단어 사이 연결 구간 표시·멈춤·떨림 필터, 손 모양 군집 파일."""
import json
import math
import random

from ksl.motion import MotionDict, _despike, _hand_basis, _one_euro, _smooth_hand_rot, _stabilize_fingers, compose

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


def test_stabilize_fingers():
    """같은 손 모양 + 떨림 → 손가락 굽힘 떨림이 줄고 마디 길이는 일정, 손 모양이 바뀌면 바뀐 모양을 따라감."""
    random.seed(1)
    import numpy as np

    def hand(curl):
        pts = [[0.0, 0.0, 0.0]]
        for f, x in enumerate([-0.04, -0.02, 0.0, 0.02, 0.04]):
            p, d = np.array([x, -0.05 if f else -0.02, 0.0]), np.array([0.0, -1.0, 0.0])
            for j in range(4):
                a = curl * (j > 0)
                d = np.array([0.0, -math.cos(a * j), -math.sin(a * j)])
                p = p + 0.025 * d if j else p
                pts.append([float(v) + random.gauss(0, 0.004) for v in p])
        return pts

    frames = [{"rh": hand(0.1 if i < 15 else 0.9)} for i in range(30)]
    out = _stabilize_fingers(frames)

    def bend(h, a, b, c):
        u, v = np.subtract(h[b], h[a]), np.subtract(h[c], h[b])
        return math.degrees(math.acos(max(-1, min(1, u @ v / np.linalg.norm(u) / np.linalg.norm(v)))))
    jit = lambda fs: np.mean([abs(bend(fs[i + 1]["rh"], 5, 6, 7) - 2 * bend(fs[i]["rh"], 5, 6, 7) + bend(fs[i - 1]["rh"], 5, 6, 7))
                              for i in range(1, 29) if i not in (14, 15)])
    assert jit(out) < jit(frames) * 0.5
    L = [math.dist(f["rh"][7], f["rh"][8]) for f in out]
    assert max(L) - min(L) < 1e-3
    assert bend(out[25]["rh"], 5, 6, 7) > bend(out[5]["rh"], 5, 6, 7) + 20     # 굽힌 모양으로 바뀜


def test_finger_pose_human_range():
    """손가락이 뒤로 접힌(가운데 마디 160°) 추정 → 사람 손가락 범위(가운데 마디 110°) 안으로."""
    import numpy as np
    def hand(fold):
        pts = [[0.0, 0.0, 0.0]]
        for x in (-0.04, -0.02, 0.0, 0.02, 0.04):
            base = np.array([x, -0.08, 0.0])
            pts += [list(base), list(base + [0, -0.03, 0]), list(base + [0, -0.03 + 0.025 * fold[0], 0.025 * fold[1]]),
                    list(base + [0, -0.03 + 0.04 * fold[0], 0.04 * fold[1]])]
        return pts
    ok = [{"rh": hand((-1, 0))} for _ in range(4)]                   # 편 손
    bad = {"rh": hand((math.cos(math.radians(20)), -math.sin(math.radians(20))))}   # 160° 로 접힘
    out = _stabilize_fingers(ok + [bad] + ok)
    P = np.array(out[4]["rh"])
    u, v = P[6] - P[5], P[7] - P[6]
    assert math.degrees(math.acos(u @ v / np.linalg.norm(u) / np.linalg.norm(v))) < 115


def test_removed_glosses(tmp_path):
    """검수에서 뺀 글로스는 동작사전에 없고, 다시 저장도 안 됨. 새 파일 번호는 지운 번호와 겹치지 않음."""
    from ksl.motion import REMOVED
    for g in REMOVED:
        assert md.resolve(g) is None
    MotionDict.save_clip(tmp_path, "가1", [{"body": None}])
    MotionDict.save_clip(tmp_path, "나1", [{"body": None}])
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    del idx["가1"]
    (tmp_path / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    MotionDict.save_clip(tmp_path, "다1", [{"body": None}])
    MotionDict.save_clip(tmp_path, next(iter(REMOVED)), [{"body": None}])
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert idx["다1"] != idx["나1"] and not (set(idx) & REMOVED)


def test_handshapes_file():
    d = json.loads((ROOT / "web/handshapes.json").read_text(encoding="utf-8"))
    assert len(d["centers"]) >= 8 and all(len(c) == 15 for c in d["centers"])
    assert 5 < d["scale"] < 40


def test_load_mediapipe_avatar(tmp_path):
    """MediaPipe 후처리 좌표(y 위 +, z 카메라 쪽 +, 깊이 과장) → 공통 형식: y 아래 +, 팔 길이가 3D 자료 정도로 맞춰짐."""
    import numpy as np
    from ksl.motion import load_mediapipe_avatar
    T = 10
    pose = np.zeros((T, 33, 3))
    pose[:, 11] = [0.5, 0, 0]; pose[:, 12] = [-0.5, 0, 0]; pose[:, 0] = [0, 0.66, 0.5]
    pose[:, 14] = [-0.7, -0.6, 2.0]; pose[:, 16] = [-0.4, -0.2, 6.0]                 # 오른 팔꿈치·손목: 깊이가 과장됨
    pose[:, 13] = [0.7, -0.6, 2.0]; pose[:, 15] = [0.4, -0.2, 6.0]
    hand = np.tile(np.linspace([0, 0, 0], [0.05, 0.3, 0.1], 21), (T, 1, 1))
    d = {"fps": 30, "pose": pose.tolist(), "left_hand": (hand + pose[:, 15:16]).tolist(), "right_hand": (hand + pose[:, 16:17]).tolist(),
         "left_hand_rest_weight": [1.0] * T, "right_hand_rest_weight": [0.0] * T}
    p = tmp_path / "w.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    frames, fps, dim = load_mediapipe_avatar(p)
    b = frames[0]["body"]
    assert dim == 3 and fps == 30 and frames[0]["lh"] is None and frames[0]["rh"]
    assert b[0][1] < b[1][1] and b[4][1] > b[1][1]                                    # 코는 목 위, 손목은 목 아래 (y 아래 +)
    arm = math.dist(b[2], b[3]) + math.dist(b[3], b[4])
    assert 1.2 < arm < 2.0 and b[4][2] < 0                                            # 팔 길이 정상, 손은 몸 앞 (z 음수)
