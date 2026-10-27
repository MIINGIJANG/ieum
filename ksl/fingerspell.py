"""한글 지문자: 수어 동작이 없는 단어를 손가락 글자로 (오른손).

- 글자를 자모로 나눔 (첫소리 ㅇ 은 소리가 없어 뺌, 겹자모는 data/sign/fingerspell.json 의 compose 대로)
- 자모마다 손 모양(손가락 펴기·엄지·손가락 방향·손바닥 방향)으로 손 21점을 만들어 동작 프레임으로
  → 아바타는 다른 동작과 똑같이 손 점으로 손가락·손목을 맞춤 (프레임에 "fs": true → 손 모양 끌어당김 등은 끔)
- 손 모양 표는 아직 수어 전문가 검수 전 (verified: false). 표만 고치면 코드는 그대로
좌표: 목=원점, 어깨너비=1, x 보는 사람 오른쪽 +, y 아래 +, z 카메라에서 멀어지는 쪽 + (ksl/motion.py 와 같음)
"""
import json
import math
import re
from pathlib import Path

import numpy as np

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ",
        "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]

# 손 크기 (어깨너비 단위, 동작 자료 오른손 중앙값): 손바닥(손목→손가락 뿌리), 손가락 세 마디
PALM = {1: 0.245, 2: 0.255, 3: 0.24, 4: 0.215}
SEG = (0.14, 0.082, 0.06)
FLEN = {1: 0.95, 2: 1.0, 3: 0.95, 4: 0.78}
SIDE = {1: 0.06, 2: 0.02, 3: -0.02, 4: -0.055}          # 손가락 뿌리 위치 (엄지 쪽 +)
SPREAD = {1: 1.0, 2: 0.33, 3: -0.33, 4: -1.0}
CURL = {"e": (4, 4, 2), "f": (85, 100, 60), "c": (35, 45, 30), "b": (80, 5, 0), "h": (5, 95, 70)}
# 손 가운데 자리 (손가락 방향마다): 위로는 오른쪽 어깨 앞, 수평은 가슴 앞 가운데 쪽, 아래로는 가슴 높이 (턱에 닿지 않게)
CENTERS = {"up": (-0.35, 0.15, -0.5), "in": (-0.12, 0.4, -0.55), "out": (-0.45, 0.35, -0.5),
           "front": (-0.35, 0.35, -0.6), "down": (-0.35, 0.5, -0.55)}
FORE = 0.84                                             # 아래팔 길이 (어깨너비 단위, 동작 자료 중앙값)
# 손가락 방향마다 아래팔 방향: 손목은 좌우로 20° 쯤밖에 못 꺾으므로 아래팔이 손 방향을 따라가야 함
#  (out = 수어자 오른쪽 수평은 아래팔을 가슴 앞으로 가로질러야 해서 오른손으로는 무리 → 표에서는 in 을 씀)
FOREARM = {"up": (0, -1, -0.35), "in": (1, -0.3, -0.6), "out": (-1, -0.5, -0.4), "front": (0, -0.3, -1), "down": (0, 0.45, -1)}


def load_table(path="data/sign/fingerspell.json"):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def jamo(word, table):
    """'정화통' → ['ㅈ','ㅓ','ㅇ','ㅎ','ㅗ','ㅏ','ㅌ','ㅗ','ㅇ'] (+ 가 붙은 'ㄱ+' 는 옆으로 움직이는 쌍자음)."""
    comp = (table or {}).get("compose", {})
    out = []
    for ch in word:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            parts = [CHO[c // 588], JUNG[(c % 588) // 28], JONG[c % 28]]
            if parts[0] == "ㅇ":
                parts[0] = ""                            # 첫소리 ㅇ 은 소리가 없음
        elif ch in CHO or ch in JUNG or ch in JONG:
            parts = [ch]
        else:
            continue
        for p in parts:
            if p:
                out += comp.get(p, [p])
    return out


def spell_text(gloss):
    """글로스 → 지문자로 쓸 말: 'FS:정화통' → '정화통', '때2' → '때', '고정하다' → '고정', '닦다' → '닦'."""
    g = gloss[3:] if gloss.startswith("FS:") else gloss
    g = re.sub(r"[_\-]?\d+#?$", "", g)
    if g.endswith("하다") and len(g) > 2:
        g = g[:-2]
    elif g.endswith("다") and len(g) > 1:
        g = g[:-1]
    return g


def _rot(axis, ang):
    a = np.asarray(axis, float); a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def _unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-9)


def hand_points(shape):
    """손 모양 → 오른손 21점 (손목 기준, 화면 좌표). 기준 자세: 손가락 위, 손바닥이 보는 사람 쪽."""
    u, n = np.array([0.0, -1.0, 0.0]), np.array([0.0, 0.0, -1.0])     # 손가락 방향, 손바닥 방향
    t = np.cross(u, n)                                                   # 엄지 쪽
    P = [np.zeros(3)] * 21
    fingers = shape.get("fingers", "eeee")
    spread = math.radians(shape.get("spread", 6))
    for fi in (1, 2, 3, 4):
        base = u * PALM[fi] + t * SIDE[fi]
        d0 = _rot(n, -spread * SPREAD[fi]) @ u if spread else u        # 엄지 쪽으로 벌림
        flex = [math.radians(x) for x in CURL[fingers[fi - 1]]]
        p, acc = base, 0.0
        P[fi * 4 + 1] = base
        for j in range(3):
            acc += flex[j]
            ax = np.cross(d0, n)
            p = p + SEG[j] * FLEN[fi] * (_rot(ax, acc) @ d0)
            P[fi * 4 + 2 + j] = p
    # 엄지
    th = shape.get("thumb", "in")
    P[1] = t * 0.06 + u * 0.05
    L = (0.1, 0.07, 0.06)
    R = _orient(shape)
    if th == "touch":
        tip = P[8]
        P[2] = P[1] + L[0] * _unit(t * 0.3 + u * 0.5 + n * 0.6)
        P[3] = P[2] + (tip - P[2]) * 0.55 + n * 0.01
        P[4] = tip + n * 0.012
    else:
        if th in ("up_w", "down_w"):                                   # 화면 위·아래로 (손 방향과 상관없이)
            w = np.array([0.0, -1.0 if th == "up_w" else 1.0, 0.0])
            dirs = [R.T @ w] * 3
        elif th == "out":
            dirs = [_unit(t * 0.85 + u * 0.5)] * 3
        elif th == "up":
            dirs = [_unit(u * 0.9 + t * 0.25 + n * 0.15)] * 3
        else:                                                          # in: 손바닥 쪽으로 접어 가로지름
            dirs = [_unit(t * 0.3 + u * 0.6 + n * 0.5), _unit(-t * 0.7 + n * 0.5 + u * 0.2), _unit(-t * 0.8 + n * 0.3 - u * 0.2)]
        for j in range(3):
            P[2 + j] = P[1 + j] + L[j] * dirs[j]
    return [R @ p for p in P], R @ u


def _orient(shape):
    """기준 자세 → 손 방향(dir)·손바닥 방향(palm) 회전."""
    u, n = np.array([0.0, -1.0, 0.0]), np.array([0.0, 0.0, -1.0])
    roll = {"front": 0.0, "back": math.pi, "side": math.pi / 2}[shape.get("palm", "front")]
    Rr = _rot(u, roll)
    target = {"up": u, "down": -u, "out": np.array([-1.0, 0, 0]), "in": np.array([1.0, 0, 0]), "front": n}[shape.get("dir", "up")]
    if np.allclose(target, u):
        Rd = np.eye(3)
    elif np.allclose(target, -u):
        Rd = _rot(n, math.pi)                                          # 화면 안에서 뒤집기
    else:
        ax = np.cross(u, target)
        Rd = _rot(ax, math.acos(float(np.clip(u @ target, -1, 1))))
    return Rd @ Rr


def _body(wrist, d="up"):
    """지문자 할 때 몸 8점: 오른손은 손목 자리, 팔꿈치는 손가락 방향에 맞춘 아래팔 쪽, 왼손은 내림 (손 모양 없음 → 힘 뺀 손)."""
    w = np.asarray(wrist, float)
    e = w - FORE * _unit(FOREARM.get(d, FOREARM["up"]))
    r = lambda v: [round(float(x), 4) for x in v]
    return [[0, -0.5, -0.4], [0, 0, 0], [-0.5, 0, 0], r(e), r(w), [0.5, 0, 0], [0.58, 0.85, 0], [0.55, 1.6, -0.05]]


def frames_for(word, table, fps=30):
    """말 → 지문자 프레임들. 자모마다 hold 초 멈추고, 다음 자모로 move 초에 걸쳐 넘어감. 손이 맞는 자모가 없으면 []."""
    if not table:
        return []
    seq = [j for j in jamo(word, table) if j.rstrip("+") in table["shapes"]]
    if not seq:
        return []
    hold, move = table.get("hold", 0.3), table.get("move", 0.1)
    if len(seq) > 8:                                                   # 긴 말은 조금 빠르게
        hold *= 0.75
    nh, nm = max(2, round(hold * fps)), max(1, round(move * fps))
    poses = []
    for j in seq:
        pts, _ = hand_points(table["shapes"][j.rstrip("+")])
        c = np.mean([pts[k] for k in (0, 5, 9, 13, 17)], 0)
        poses.append((np.array(pts) - c, j.endswith("+"), table["shapes"][j.rstrip("+")].get("dir", "up")))   # 손 가운데 기준
    out = []

    def frame(rel, shift, fdir, c=None):
        C = np.array(CENTERS.get(fdir, CENTERS["up"])) if c is None else c
        wrist = C + rel[0] + shift
        hand = [[round(float(v), 4) for v in (C + p + shift)] for p in rel]
        return {"body": _body(wrist, fdir), "rh": hand, "lh": None, "fs": True}

    def blend_body(f, g, a):                                           # 넘어가는 동안 팔꿈치도 섞음
        f["body"] = [[round(x + (y - x) * a, 4) for x, y in zip(p, q)] for p, q in zip(f["body"], g["body"])]
        return f

    for k, (rel, dbl, fdir) in enumerate(poses):
        for i in range(nh):
            s = np.array([-0.12, 0, 0]) * (_mj((i + 1) / nh) if dbl else 0)   # 쌍자음: 옆으로 움직임
            out.append(frame(rel, s, fdir))
        if k + 1 < len(poses):
            nxt, _, ndir = poses[k + 1]
            s0 = np.array([-0.12, 0, 0]) if dbl else np.zeros(3)
            for i in range(nm):
                a = _mj((i + 1) / (nm + 1))
                mid = rel * (1 - a) + nxt * a
                c = np.array(CENTERS.get(fdir, CENTERS["up"])) * (1 - a) + np.array(CENTERS.get(ndir, CENTERS["up"])) * a
                out.append(blend_body(frame(mid, s0 * (1 - a), fdir, c), frame(mid, s0 * (1 - a), ndir, c), a))
    return out


def _mj(t):
    return t * t * t * (10 - 15 * t + 6 * t * t)
