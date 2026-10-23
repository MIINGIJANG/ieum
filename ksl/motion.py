"""수어 동작(키포인트) 데이터: 공통 형식 · 변환 · 동작사전 · 이어붙이기.

공통 프레임 형식 (아바타/웹이 받는 형태)
    {"body": [[x, y(, z)], ... 8개], "lh": [[x, y(, z)], ... 21개] | null, "rh": [...] | null}
    - body 순서: BODY_JOINTS (코, 목, 오른어깨, 오른팔꿈치, 오른손목, 왼어깨, 왼팔꿈치, 왼손목)
    - 손 21점 순서는 OpenPose 손 = MediaPipe 손 (손목 0, 엄지 1-4, 검지 5-8, 중지 9-12, 약지 13-16, 소지 17-20)
    - 좌표 정규화: 목(neck)=원점, 어깨너비=1, x 오른쪽 +, y 아래 +, z 카메라에서 멀어지는 쪽 +
      (AI Hub 3D 키포인트의 카메라 좌표계 그대로. 2D 데이터는 z 없음)
      → 사람·카메라 거리가 달라도 같은 크기로 이어붙일 수 있음
    - 'r'/'l' 은 수어자 본인 기준 오른손/왼손
"""
import json
import math
import re
from pathlib import Path

BODY_JOINTS = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow", "l_wrist"]
HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
              (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]
BODY_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (1, 5), (5, 6), (6, 7)]
FPS = 30


# ---------------------------------------------------------------- 정규화
def normalize(frames):
    """프레임 리스트를 '클립 전체 기준' 하나의 원점/스케일로 정규화 (클립 안 움직임은 보존)."""
    necks, widths = [], []
    for f in frames:
        b = f["body"]
        if b and b[1] and b[2] and b[5]:
            necks.append(b[1])
            widths.append(math.dist(b[2], b[5]))
    if not necks:
        return frames
    med = lambda xs: sorted(xs)[len(xs) // 2]
    dim = min(len(n) for n in necks)
    c = [med([n[i] for n in necks]) for i in range(dim)]
    s = med(widths) or 1.0

    def tr(pts):
        if pts is None:
            return None
        return [None if p is None else [round((p[i] - c[i]) / s, 4) for i in range(min(dim, len(p)))] for p in pts]

    out = []
    for f in frames:
        g = {k: v for k, v in f.items() if k not in ("body", "lh", "rh", "face")}
        g.update({"body": tr(f["body"]), "lh": tr(f.get("lh")), "rh": tr(f.get("rh"))})
        out.append(g)
    return out


# ---------------------------------------------------------------- 2D → 3D (깊이 추정)
HAND_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10), (10, 11), (11, 12),
              (0, 13), (13, 14), (14, 15), (15, 16), (0, 17), (17, 18), (18, 19), (19, 20)]
ARM_BONES = [(2, 3), (3, 4), (5, 6), (6, 7)]          # 오른 어깨-팔꿈치, 팔꿈치-손목, 왼쪽 같음


def bone_lengths(clips3d):
    """3D 클립들(정규화된 것)에서 뼈 길이 중앙값 → 2D 클립 깊이 추정에 사용."""
    import statistics
    arm = {b: [] for b in ARM_BONES}
    hand = {b: [] for b in HAND_BONES}
    for frames in clips3d:
        for f in frames:
            b = f.get("body") or []
            for i, j in ARM_BONES:
                if i < len(b) and j < len(b) and b[i] and b[j] and len(b[i]) > 2:
                    arm[(i, j)].append(math.dist(b[i], b[j]))
            for k in ("lh", "rh"):
                h = f.get(k)
                if not h:
                    continue
                for i, j in HAND_BONES:
                    if h[i] and h[j] and len(h[i]) > 2:
                        hand[(i, j)].append(math.dist(h[i], h[j]))
    med = lambda xs, d: statistics.median(xs) if xs else d
    return {"arm": {b: med(v, 0.45) for b, v in arm.items()}, "hand": {b: med(v, 0.06) for b, v in hand.items()}}


def lift_2d(frames, L):
    """2D(정규화) 클립에 깊이를 추정해 3D 로: 3D 클립의 뼈 길이를 알고 있으므로
    화면에서 짧아 보이는 만큼 앞(카메라 쪽, z 음수)으로 나온 것으로 봄 (수어는 몸 앞에서 하므로 앞쪽으로 가정).
    손가락은 손바닥 방향으로 같은 비율만큼만 앞으로 (뒤로 꺾인 손가락이 생기지 않게)."""
    def depth(a, b, length):
        d = math.dist(a[:2], b[:2])
        return math.sqrt(max(0.0, length * length - d * d))

    # 뼈 길이: 이 클립 안에서 화면상 가장 길게 보인 길이(= 화면과 나란할 때의 실제 길이)와
    # 3D 자료의 길이를 클립 크기에 맞춘 값 중 큰 것 (2D 정규화 크기가 3D 와 조금 달라서 클립 기준으로 맞춤)
    def p95(xs):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(0.95 * len(xs)))] if xs else 0.0
    arm_len, hand_len = {}, {}
    for (i, j) in ARM_BONES:
        arm_len[(i, j)] = max(p95([math.dist(f["body"][i][:2], f["body"][j][:2]) for f in frames
                                   if f.get("body") and f["body"][i] and f["body"][j]]), L["arm"][(i, j)])
    palm2d = p95([math.dist(f[k][0][:2], f[k][9][:2]) for f in frames for k in ("rh", "lh") if f.get(k) and f[k][0] and f[k][9]])
    hs = palm2d / L["hand"][(0, 9)] if palm2d else 1.0
    for (i, j) in HAND_BONES:
        hand_len[(i, j)] = max(p95([math.dist(f[k][i][:2], f[k][j][:2]) for f in frames for k in ("rh", "lh")
                                    if f.get(k) and f[k][i] and f[k][j]]), L["hand"][(i, j)] * hs)
    out = []
    for f in frames:
        g = dict(f)
        b = f.get("body")
        if b:
            nb = [None if p is None else [p[0], p[1], 0.0] for p in b]
            if nb[0]:
                nb[0][2] = -0.4                                    # 코는 목보다 앞
            for sho, elb, wri in ((2, 3, 4), (5, 6, 7)):
                if nb[sho] and nb[elb]:
                    nb[elb][2] = round(nb[sho][2] - 0.5 * depth(nb[sho], nb[elb], arm_len[(sho, elb)]), 4)
                if nb[elb] and nb[wri]:
                    nb[wri][2] = round(nb[elb][2] - depth(nb[elb], nb[wri], arm_len[(elb, wri)]), 4)
            g["body"] = nb
        for k, wi in (("rh", 4), ("lh", 7)):
            h = f.get(k)
            if not h:
                continue
            wz = g["body"][wi][2] if g.get("body") and g["body"][wi] else 0.0
            nh = [None if p is None else [p[0], p[1], 0.0] for p in h]
            if nh[0]:
                nh[0][2] = wz
            for i, j in HAND_BONES:                                 # 뿌리→끝 순서라 부모 깊이가 먼저 정해짐
                if nh[i] and nh[j]:
                    nh[j][2] = round(nh[i][2] - 0.6 * depth(nh[i], nh[j], hand_len[(i, j)]), 4)
            g[k] = nh
        out.append(g)
    return out


def load_team_clip(path):
    """조원 형식(글로스 1개 = JSON 1개) 읽기.

    {"gloss", "fps", "landmarks": {"pose_keypoints_3d": [[75]...], "hand_right_keypoints_3d": [[63]...], ...}}
    - *_3d: 점마다 x,y,z (mm, 카메라 좌표계, 신뢰도 없음)
    - *_2d: 점마다 x,y,신뢰도 (픽셀)
    반환: (frames, fps, dim)  dim = 3 또는 2
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    L = d["landmarks"]
    if "pose" in L:      # 조원 형식 2 (2026-10-10): landmarks = {pose, face, right_hand, left_hand} 픽셀 x,y,신뢰도
        L = {"pose_keypoints_2d": L["pose"], "face_keypoints_2d": L.get("face") or [],
             "hand_right_keypoints_2d": L.get("right_hand") or [], "hand_left_keypoints_2d": L.get("left_hand") or []}
    dim = 3 if any(k.endswith("_3d") for k in L) else 2
    sfx = "_3d" if dim == 3 else "_2d"

    def pts(row, n):
        if not row:
            return None
        out = []
        for i in range(n):
            x, y, t = row[3 * i: 3 * i + 3]
            if dim == 3:
                out.append([x, y, t] if (x or y or t) else None)
            else:
                out.append([x, y] if t >= 0.1 and (x or y) else None)
        return out if sum(p is not None for p in out) >= 5 else None

    pose = L.get("pose_keypoints" + sfx) or []
    rh = L.get("hand_right_keypoints" + sfx) or []
    lh = L.get("hand_left_keypoints" + sfx) or []
    face = L.get("face_keypoints" + sfx) or []
    frames = []
    for i in range(len(pose)):
        b25 = pts(pose[i], 25)
        frames.append({"body": b25[:8] if b25 else None,
                       "rh": pts(rh[i], 21) if i < len(rh) else None,
                       "lh": pts(lh[i], 21) if i < len(lh) else None,
                       "face": pts(face[i], 70) if i < len(face) else None})
    return frames, float(d.get("fps", FPS)), dim


# ---------------------------------------------------------------- 표정 (비수지 표현)
# OpenPose 얼굴 70점: 17-21 오른눈썹, 22-26 왼눈썹, 36-41 오른눈, 42-47 왼눈, 48-59 바깥입술, 60-67 안쪽입술
def _mid(ps):
    ps = [p for p in ps if p]
    return [sum(c) / len(ps) for c in zip(*ps)] if ps else None


def face_features(f):
    """얼굴 점 → 크기에 상관없는 원시 특징 (눈 사이 거리로 나눔). 없으면 None."""
    if not f:
        return None
    need = [36, 39, 42, 45, 48, 54, 62, 66, 19, 24, 37, 38, 40, 41, 43, 44, 46, 47]
    if any(f[i] is None for i in need):
        return None
    re_, le = _mid(f[36:42]), _mid(f[42:48])
    iod = math.dist(re_, le) or 1.0
    eye_open = (math.dist(f[37], f[41]) + math.dist(f[38], f[40]) + math.dist(f[43], f[47]) + math.dist(f[44], f[46])) / (2 * (math.dist(f[36], f[39]) + math.dist(f[42], f[45])))
    feat = {
        "mouth_open": math.dist(f[62], f[66]) / iod,
        "mouth_width": math.dist(f[48], f[54]) / iod,
        "eye_open": eye_open,
        "brow": (math.dist(f[19], re_) + math.dist(f[24], le)) / (2 * iod),
    }
    if len(re_) == 3:   # 3D 면 머리 방향 (three.js 좌표: x 오른쪽, y 위, z 카메라 쪽)
        V = lambda p: [p[0], -p[1], -p[2]]
        x = [a - b for a, b in zip(V(le), V(re_))]                        # 오른눈 → 왼눈
        mouth = _mid([f[48], f[54], f[62], f[66]])
        y = [a - b for a, b in zip(V(_mid([re_, le])), V(mouth))]         # 입 → 눈
        fwd = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
        n = math.sqrt(sum(c * c for c in fwd)) or 1
        fwd = [c / n for c in fwd]
        feat["yaw"] = math.degrees(math.atan2(fwd[0], fwd[2]))
        feat["pitch"] = math.degrees(math.asin(max(-1, min(1, fwd[1]))))
        feat["roll"] = math.degrees(math.atan2(x[1], x[0]))
    return feat


def calibrate_expressions(clips):
    """모든 클립의 얼굴 특징 분포로 0~1 표정 값과 머리 각도를 계산해 frame['expr'], frame['head'] 에 기록.
    (사람마다 얼굴 생김새가 달라서, 절대값 대신 전체 데이터에서의 상대 위치로 판단)"""
    vals = {}
    for frames in clips:
        for f in frames:
            f["_ff"] = face_features(f.get("face"))
            for k, v in (f["_ff"] or {}).items():
                vals.setdefault(k, []).append(v)
    pct = lambda k, q: sorted(vals[k])[min(len(vals[k]) - 1, int(q * len(vals[k])))] if vals.get(k) else 0
    clip01 = lambda v: round(max(0.0, min(1.0, v)), 3)
    for frames in clips:
        for f in frames:
            ff = f.pop("_ff")
            if not ff:
                continue
            mo, mw, eo, br = ff["mouth_open"], ff["mouth_width"], ff["eye_open"], ff["brow"]
            span = lambda a, b: (b - a) or 1e-6
            f["expr"] = {
                "aa": clip01((mo - pct("mouth_open", .3)) / span(pct("mouth_open", .3), pct("mouth_open", .97))),
                "ou": clip01((pct("mouth_width", .5) - mw) / span(pct("mouth_width", .03), pct("mouth_width", .5)) * 0.8),
                "ee": clip01((mw - pct("mouth_width", .5)) / span(pct("mouth_width", .5), pct("mouth_width", .97)) * 0.6),
                "blink": clip01((pct("eye_open", .5) - eo) / span(pct("eye_open", .03), pct("eye_open", .5)) * 0.9),
                "surprised": clip01((br - pct("brow", .5)) / span(pct("brow", .5), pct("brow", .97)) * 0.7),
                "angry": clip01((pct("brow", .5) - br) / span(pct("brow", .03), pct("brow", .5)) * 0.5),
            }
            if "yaw" in ff:
                f["head"] = [round(max(-30, min(30, ff[k] - pct(k, .5))), 1) for k in ("yaw", "pitch", "roll")]



# ---------------------------------------------------------------- OpenPose (AI Hub) 읽기
def _triples(arr, conf_th, n_points):
    if not arr:
        return None
    step = len(arr) // n_points if len(arr) % n_points == 0 else 3   # 2D: x,y,c / 3D: x,y,z,c
    pts = []
    for i in range(0, len(arr) - step + 1, step):
        x, y, c = arr[i], arr[i + 1], arr[i + step - 1]
        pts.append([x, y] if c >= conf_th and (x or y) else None)
    return pts


def openpose_frame(d, conf_th=0.1):
    """OpenPose JSON 한 프레임 → 공통 형식. people 이 list 든 dict 든 처리, *_2d 우선."""
    p = d.get("people", d)
    if isinstance(p, list):
        if not p:
            return {"body": None, "lh": None, "rh": None}
        p = p[0]
    get = lambda k: p.get(f"{k}_2d") or p.get(k) or p.get(f"{k}_3d")
    body25 = _triples(get("pose_keypoints"), conf_th, 25)
    body = [body25[i] if body25 and i < len(body25) else None for i in range(8)] if body25 else None
    hands = {s: _triples(get(f"hand_{s}_keypoints"), conf_th, 21) for s in ("left", "right")}
    for s in hands:   # 손 점이 거의 없으면 손 안 보임 처리
        if hands[s] and sum(x is not None for x in hands[s]) < 5:
            hands[s] = None
    return {"body": body, "lh": hands["left"], "rh": hands["right"]}


def load_openpose(path):
    """폴더(프레임별 *_keypoints.json) 또는 프레임 배열 JSON 파일 → 프레임 리스트."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.json"), key=lambda f: [int(x) for x in re.findall(r"\d+", f.stem)] or [0])
        return [openpose_frame(json.loads(f.read_text(encoding="utf-8"))) for f in files]
    d = json.loads(path.read_text(encoding="utf-8"))
    frames = d if isinstance(d, list) else d.get("frames") or d.get("data") or [d]
    return [openpose_frame(f) for f in frames]


# ---------------------------------------------------------------- MediaPipe 결과 → 공통 형식
def mediapipe_frame(pose_lms, hand_lms_list, w, h):
    """pose_lms: 33개 랜드마크(정규화 좌표), hand_lms_list: [21개 랜드마크, ...]."""
    if not pose_lms:
        return {"body": None, "lh": None, "rh": None}
    P = lambda i: [pose_lms[i].x * w, pose_lms[i].y * h]
    ls, rs = P(11), P(12)   # MediaPipe 11=왼어깨, 12=오른어깨 (수어자 기준)
    neck = [(ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2]
    body = [P(0), neck, rs, P(14), P(16), ls, P(13), P(15)]
    lh = rh = None
    for hand in hand_lms_list or []:
        pts = [[p.x * w, p.y * h] for p in hand]
        # MediaPipe 의 Left/Right 라벨은 셀카(좌우반전) 기준이라 헷갈림 → 손목과 더 가까운 쪽으로 배정
        d_r, d_l = math.dist(pts[0], body[4]), math.dist(pts[0], body[7])
        if d_r <= d_l and rh is None:
            rh = pts
        elif lh is None:
            lh = pts
        else:
            rh = rh or pts
    return {"body": body, "lh": lh, "rh": rh}


def trim_idle(frames, pad=3):
    """앞뒤로 손이 안 보이는 구간 잘라내기 (사전 영상의 준비/마무리 자세 제거)."""
    vis = [i for i, f in enumerate(frames) if f.get("lh") or f.get("rh")]
    if not vis:
        return frames
    return frames[max(0, vis[0] - pad): vis[-1] + pad + 1]


# ---------------------------------------------------------------- 동작사전
def _strip_num(g):
    return re.sub(r"[_\-]?\d+#?$", "", g)


class MotionDict:
    """motions/ 폴더: index.json + 글로스별 <id>.json ({"gloss","fps","frames","source"})."""

    def __init__(self, root):
        self.root = Path(root)
        idx = self.root / "index.json"
        self.index = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {}
        self._cache = {}
        self._norm = {}
        for g in self.index:          # '위험1', '낙하물0#' → '위험', '낙하물' 로도 찾을 수 있게
            self._norm.setdefault(_strip_num(g), g)

    def resolve(self, g):
        return g if g in self.index else self._norm.get(_strip_num(g))

    def __contains__(self, g):
        return self.resolve(g) is not None

    def get(self, g):
        g = self.resolve(g)
        if g is None:
            return None
        if g not in self._cache:
            self._cache[g] = json.loads((self.root / self.index[g]).read_text(encoding="utf-8"))
        return self._cache[g]

    @staticmethod
    def save_clip(root, gloss, frames, fps=FPS, source=None):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        idx_p = root / "index.json"
        index = json.loads(idx_p.read_text(encoding="utf-8")) if idx_p.exists() else {}
        name = index.get(gloss) or f"{len(index):05d}.json"   # 파일명은 번호 (한글/특수문자 경로 문제 방지)
        (root / name).write_text(json.dumps({"gloss": gloss, "fps": fps, "frames": frames, "source": source},
                                            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index[gloss] = name
        idx_p.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 이어붙이기
def _resample(frames, n):
    """n 프레임으로 다시 뽑기. 프레임 사이는 보간 (느리게 재생할 때 같은 프레임이 반복돼 끊겨 보이지 않게)."""
    if n <= 0 or not frames:
        return []
    if len(frames) == n:
        return frames
    out = []
    for i in range(n):
        x = i * (len(frames) - 1) / max(n - 1, 1)
        k = min(int(x), len(frames) - 2) if len(frames) > 1 else 0
        out.append(_lerp(frames[k], frames[k + 1], x - k) if len(frames) > 1 else frames[0])
    return out


def _one_euro(frames, fps=FPS, min_cutoff=1.5, beta=0.5, d_cutoff=1.0):
    """관절 좌표 떨림 줄이기: One Euro 필터 (Casiez 2012).
    천천히 움직일 땐 많이 걸러 떨림을 없애고, 빠르게 움직일 땐 덜 걸러 늦게 따라가지 않음
    (이동평균은 둘 중 하나만 됨: 많이 거르면 빠른 동작이 뭉개지고, 적게 거르면 떨림이 남음).
    단위: 좌표는 어깨너비=1, 속도는 어깨너비/초. 점이 안 보인 프레임(None)에서는 그 점의 필터를 처음부터 다시 시작."""
    if len(frames) < 3:
        return frames
    te = 1.0 / fps
    alpha = lambda cutoff: 1.0 / (1.0 + (1.0 / (2 * math.pi * cutoff)) / te)
    out = [dict(f) for f in frames]
    for k in ("body", "lh", "rh"):
        state = {}                                         # (점 번호) → (이전 값, 이전 속도)
        for f in out:
            pts = f.get(k)
            if not pts:
                state.clear()
                continue
            new = []
            for j, p in enumerate(pts):
                if p is None:
                    state.pop(j, None)
                    new.append(None)
                    continue
                prev = state.get(j)
                if prev is None:
                    state[j] = (list(p), [0.0] * len(p))
                    new.append(list(p))
                    continue
                xp, dxp = prev
                dx = [(a - b) / te for a, b in zip(p, xp)]
                ad = alpha(d_cutoff)
                dxh = [ad * a + (1 - ad) * b for a, b in zip(dx, dxp)]
                speed = math.sqrt(sum(v * v for v in dxh[:2]))
                a = alpha(min_cutoff + beta * speed)
                xh = [a * u + (1 - a) * v for u, v in zip(p, xp)]
                state[j] = (xh, dxh)
                new.append([round(v, 4) for v in xh])
            f[k] = new
    return out


def _relax_low_hands(frames, y_rest=1.15):
    """수어를 하지 않고 아래로 내린 손(손목이 허리 아래)은 손 모양 데이터를 버림 → 아바타가 힘 뺀 손으로 둠.
    (내린 손의 추정 좌표는 떨림이 커서 손목이 꺾이거나 다리에 파묻혀 어색해 보임)"""
    out = []
    for f in frames:
        b = f.get("body")
        if b:
            f = dict(f)
            for wi, k in ((4, "rh"), (7, "lh")):
                if wi < len(b) and b[wi] and b[wi][1] > y_rest:
                    f[k] = None
        out.append(f)
    return out


def _fix_hands(frames, ratio=0.7):
    """손 점이 한꺼번에 뭉친 프레임(손바닥 길이가 평소의 70% 미만 = 추정 실패)은 가장 가까운 정상 프레임의 손으로 바꿈."""
    import statistics
    out = [dict(f) for f in frames]
    for k in ("rh", "lh"):
        L = [math.dist(f[k][0], f[k][9]) if f.get(k) and f[k][0] and f[k][9] else None for f in frames]
        good = [x for x in L if x]
        if len(good) < 3:
            continue
        med = statistics.median(good)
        ok = [x is not None and x >= ratio * med for x in L]
        for i, f in enumerate(out):
            if f.get(k) and not ok[i]:
                near = min((j for j in range(len(out)) if ok[j]), key=lambda j: abs(j - i), default=None)
                f[k] = frames[near][k] if near is not None else None
    return out


def _hand_basis(h):
    """손 방향(3×3 회전): x = 손목→가운데 손가락 뿌리, z = x × (검지 뿌리→새끼 뿌리). 점이 모자라면 None."""
    import numpy as np
    if not h or any(i >= len(h) or h[i] is None or len(h[i]) < 3 for i in (0, 5, 9, 17)):
        return None
    f = np.subtract(h[9], h[0]); sd = np.subtract(h[17], h[5])
    z = np.cross(f, sd)
    if np.linalg.norm(f) < 1e-6 or np.linalg.norm(z) < 1e-6:
        return None
    x = f / np.linalg.norm(f); z = z / np.linalg.norm(z)
    return np.stack([x, np.cross(z, x), z], 1)


def _smooth_hand_rot(frames, fps=FPS, half=2, vmax=540.0):
    """손 방향 튐 잡기. 3D 추정 손은 손바닥이 옆을 볼 때 앞뒤가 뒤집혀 한 프레임에 100°~170° 도는 일이 잦음
    (실제 손목은 그렇게 못 돎) → 아바타 손목이 팍 튐.
    1) 앞뒤 half 프레임 안에서 회전의 '중앙값'(Weiszfeld, 튀는 값에 끌려가지 않음)
    2) 프레임당 회전을 vmax(도/초)로 제한, 앞→뒤·뒤→앞 두 번 해서 평균 (한쪽으로 늦어지지 않게)
    손가락 모양은 그대로 두고 손 전체를 손목 둘레로 돌려서 바꿈."""
    import numpy as np

    def proj(M):                                   # 가장 가까운 회전 행렬
        u, _, vt = np.linalg.svd(M)
        R = u @ vt
        if np.linalg.det(R) < 0:
            u[:, -1] *= -1
            R = u @ vt
        return R

    def toward(A, B, lim):                         # A 에서 B 쪽으로 최대 lim 라디안
        D = A.T @ B
        ang = math.acos(max(-1.0, min(1.0, (np.trace(D) - 1) / 2)))
        if ang <= lim or ang < 1e-9:
            return B
        w = np.array([D[2, 1] - D[1, 2], D[0, 2] - D[2, 0], D[1, 0] - D[0, 1]])
        n = np.linalg.norm(w)
        if n < 1e-9:                               # 180° 근처: 축을 정할 수 없음 → 그대로
            return A
        k = w / n
        K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        return A @ (np.eye(3) + math.sin(lim) * K + (1 - math.cos(lim)) * K @ K)

    out = [dict(f) for f in frames]
    lim = math.radians(vmax) / fps
    for k in ("rh", "lh"):
        B = [_hand_basis(f.get(k)) for f in frames]
        i = 0
        while i < len(B):                          # 손이 계속 보이는 구간마다
            if B[i] is None:
                i += 1
                continue
            j = i
            while j < len(B) and B[j] is not None:
                j += 1
            run = B[i:j]
            med = []
            for t in range(len(run)):
                W = run[max(0, t - half): t + half + 1]
                M = proj(sum(W))
                for _ in range(6):
                    wts = [1.0 / max(1e-3, np.linalg.norm(X - M)) for X in W]
                    M = proj(sum(w * X for w, X in zip(wts, W)))
                med.append(M)
            fw, bw = [med[0]], [med[-1]]
            for t in range(1, len(med)):
                fw.append(toward(fw[-1], med[t], lim))
            for t in range(len(med) - 2, -1, -1):
                bw.append(toward(bw[-1], med[t], lim))
            bw.reverse()
            for t in range(len(run)):
                Rn = proj(fw[t] + bw[t])
                Q = Rn @ run[t].T
                if (np.trace(Q) - 1) / 2 > math.cos(math.radians(0.5)):
                    continue
                h = frames[i + t][k]
                o = np.array(h[0])
                out[i + t][k] = [None if p is None else [round(v, 4) for v in (o + Q @ (np.array(p) - o))] for p in h]
            i = j
    return out


def _filter_face(frames, win=5):
    """고개 각도·표정: 손이 얼굴을 가리면 얼굴 추정이 튐(예: 고개 -30° → +29°) → 중앙값으로 튀는 값 제거 후 부드럽게."""
    import statistics
    n = len(frames)
    if n < 3:
        return frames
    out = [dict(f) for f in frames]
    h = win // 2
    heads = [f.get("head") for f in frames]
    if all(heads):
        med = [[statistics.median(heads[j][c] for j in range(max(0, i - h), min(n, i + h + 1))) for c in range(3)]
               for i in range(n)]
        for i in range(n):
            a, b, c = med[max(0, i - 1)], med[i], med[min(n - 1, i + 1)]
            out[i]["head"] = [round((x + 2 * y + z) / 4, 1) for x, y, z in zip(a, b, c)]
    exprs = [f.get("expr") for f in frames]
    if all(exprs):
        for k in exprs[0]:
            med = [statistics.median(exprs[j].get(k, 0) for j in range(max(0, i - h), min(n, i + h + 1))) for i in range(n)]
            for i in range(n):
                out[i]["expr"] = dict(out[i]["expr"])
                out[i]["expr"][k] = round(med[i], 3)
    return out


def _ease(t):
    return t * t * (3 - 2 * t)     # 천천히 출발해서 천천히 멈춤 (smoothstep)


def _min_jerk(t):
    return t * t * t * (10 - 15 * t + 6 * t * t)     # 가속도 변화가 가장 작은 곡선 (사람이 손을 옮길 때와 비슷)


def _wrist_gap(fa, fb):
    """두 프레임 손목 사이 거리 중 큰 것 (어깨너비 단위) → 단어 사이 연결 시간 정할 때."""
    ba, bb = fa.get("body") or [], fb.get("body") or []
    d = [math.dist(ba[i][:2], bb[i][:2]) for i in (4, 7) if i < len(ba) and i < len(bb) and ba[i] and bb[i]]
    return max(d) if d else 0.5


def _hand_turn(fa, fb):
    """두 프레임 손 방향 차이 중 큰 것 (0~1, 1 = 180°) → 손을 많이 돌려야 하면 연결을 길게."""
    import numpy as np
    out = 0.0
    for k in ("rh", "lh"):
        A, B = _hand_basis(fa.get(k)), _hand_basis(fb.get(k))
        if A is not None and B is not None:
            out = max(out, math.acos(max(-1.0, min(1.0, (np.trace(A.T @ B) - 1) / 2))) / math.pi)
    return out


def _lerp_pts(a, b, t):
    if a is None or b is None:
        return b if t >= 0.5 else a
    return [None if (p is None or q is None) else [round(u + (v - u) * t, 4) for u, v in zip(p, q)]
            for p, q in zip(a, b)]


def _lerp(fa, fb, t):
    out = {k: _lerp_pts(fa.get(k), fb.get(k), t) for k in ("body", "lh", "rh")}
    ea, eb = fa.get("expr"), fb.get("expr")
    if ea or eb:
        ea, eb = ea or {}, eb or {}
        out["expr"] = {k: round(ea.get(k, 0) + (eb.get(k, 0) - ea.get(k, 0)) * t, 3) for k in set(ea) | set(eb)}
    ha, hb = fa.get("head"), fb.get("head")
    if ha or hb:
        ha, hb = ha or [0, 0, 0], hb or [0, 0, 0]
        out["head"] = [round(a + (b - a) * t, 1) for a, b in zip(ha, hb)]
    return out


def neutral_frame():
    """손을 내린 기본 자세 (동작 데이터가 없는 글로스 자리에 사용)."""
    body = [[0, -0.5, -0.4], [0, 0, 0], [-0.5, 0, 0], [-0.58, 0.85, 0], [-0.55, 1.6, -0.05],
            [0.5, 0, 0], [0.58, 0.85, 0], [0.55, 1.6, -0.05]]
    return {"body": body, "lh": None, "rh": None}


def compose(glosses, mdict: MotionDict, fps=FPS, transition=0.25, missing_sec=0.6, speed=0.8, skip_missing=True,
            lead=0.45, lead_out=0.8, hold=0.07):
    """글로스 순서대로 동작 클립을 이어붙임.

    - speed: 재생 속도 (조원 자료는 문장 수어에서 단어만 잘라 0.4~0.8초로 짧음 → 0.8배로 조금 천천히)
    - 클립 사이: 손이 멀리 움직이거나 손을 많이 돌릴수록 길게 (transition × 0.6~1.8), 최소 저크 곡선으로 보간.
      연결 프레임에는 tr={"a": 앞 단어 마지막 프레임, "b": 뒤 단어 첫 프레임, "t": 0~1} → 아바타는 두 자세의 '회전'을 섞음
      (관절 좌표를 직선으로 섞으면 팔이 짧아졌다 길어지고 손목이 꺾임)
    - 단어 끝에서 hold 초 동안 멈춤 (수어의 '멈춤-움직임-멈춤' 리듬: 단어 경계가 보임)
    - 처음·끝: 손을 내린 기본 자세에서 lead 초 동안 올라오고, 끝나면 lead_out 초 동안 천천히 내려감 (갑자기 튀지 않게)
    - 관절 좌표 떨림은 One Euro 필터로 줄임
    skip_missing=True: 동작이 없는 글로스는 건너뜀 (프레임 없음, segment 의 start/end = None)
    반환: {"fps", "frames", "segments": [{"gloss","start","end","has_motion"}]}  (start/end 는 프레임 번호)
    """
    frames, segments = [], []
    n_lead = max(1, round(lead * fps))
    n_out = max(1, round(lead_out * fps))

    def toward_rest(f, t):      # t=0 이면 동작 그대로, 1 이면 기본 자세. 아바타가 관절 회전을 기본 자세 쪽으로 섞음
        out = dict(f)
        out["rest"] = round(t, 3)
        if f.get("expr"):
            out["expr"] = {k: round(v * (1 - t), 3) for k, v in f["expr"].items()}
        if f.get("head"):
            out["head"] = [round(v * (1 - t), 1) for v in f["head"]]
        return out

    for g in glosses:
        clip = mdict.get(g) if mdict else None
        if clip and str(clip.get("source", "")).endswith("(2D)"):
            clip = None        # 깊이 추정을 하지 않은 2D 자료는 손가락·팔 방향이 뒤틀려 보여서 쓰지 않음 ('2D→3D' 로 저장된 것은 사용)
        if clip and clip["frames"]:
            cf = clip.get("fps", fps)
            src = _relax_low_hands(_one_euro(_smooth_hand_rot(_filter_face(_fix_hands(clip["frames"])), cf), cf))
            n = max(2, round(len(src) * fps / cf / speed))
            seg = _resample(src, n)
            has = True
        elif skip_missing:
            # 동작이 없는 글로스(지문자 FS: 포함): 재생에서 건너뜀 → 웹에서 회색 단어로만 표시
            segments.append({"gloss": g, "start": None, "end": None, "has_motion": False})
            continue
        else:
            seg = [neutral_frame()] * max(2, round(missing_sec * fps))     # 기본 자세 유지
            has = False
        if frames:
            a, b = frames[-1], seg[0]
            dur = transition * min(1.8, max(0.6, 0.5 + 0.6 * _wrist_gap(a, b) + 0.5 * _hand_turn(a, b)))
            n_tr = max(2, round(dur * fps))
            ia, ib = len(frames) - 1, len(frames) + n_tr
            for i in range(n_tr):
                u = (i + 1) / (n_tr + 1)
                f = _lerp(a, b, _min_jerk(u))
                f["tr"] = {"a": ia, "b": ib, "t": round(u, 3)}
                frames.append(f)
        elif has:
            frames += [toward_rest(seg[0], 1 - _ease(i / n_lead)) for i in range(n_lead)]   # 기본 자세 → 첫 동작
        start = len(frames)
        frames += seg
        segments.append({"gloss": g, "start": start, "end": len(frames) - 1, "has_motion": has})
        if has and hold > 0:
            frames += [dict(seg[-1])] * max(1, round(hold * fps))                       # 단어 끝 멈춤
    if frames:
        last = frames[-1]
        frames += [last] * round(0.15 * fps)                                                     # 마지막 손 모양 잠깐 유지
        frames += [toward_rest(last, _ease((i + 1) / n_out)) for i in range(n_out)]             # 마지막 동작 → 기본 자세 (천천히)
    return {"fps": fps, "frames": frames, "segments": segments}