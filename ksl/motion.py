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


def load_team_clip(path):
    """조원 형식(글로스 1개 = JSON 1개) 읽기.

    {"gloss", "fps", "landmarks": {"pose_keypoints_3d": [[75]...], "hand_right_keypoints_3d": [[63]...], ...}}
    - *_3d: 점마다 x,y,z (mm, 카메라 좌표계, 신뢰도 없음)
    - *_2d: 점마다 x,y,신뢰도 (픽셀)
    반환: (frames, fps, dim)  dim = 3 또는 2
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    L = d["landmarks"]
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
    if n <= 0 or not frames:
        return []
    if len(frames) == n:
        return frames
    return [frames[min(len(frames) - 1, round(i * (len(frames) - 1) / max(n - 1, 1)))] for i in range(n)]


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


def compose(glosses, mdict: MotionDict, fps=FPS, transition=0.2, missing_sec=0.6, speed=1.0, skip_missing=True):
    """글로스 순서대로 동작 클립을 이어붙임. 클립 사이는 transition 초 동안 선형 보간.

    skip_missing=True: 동작이 없는 글로스는 건너뜀 (프레임 없음, segment 의 start/end = None)
    반환: {"fps", "frames", "segments": [{"gloss","start","end","has_motion"}]}  (start/end 는 프레임 번호)
    """
    frames, segments = [], []
    n_tr = max(1, round(transition * fps))
    for g in glosses:
        clip = mdict.get(g) if mdict else None
        if clip and clip["frames"]:
            src = clip["frames"]
            n = max(2, round(len(src) * fps / clip.get("fps", fps) / speed))
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
            frames += [_lerp(a, b, (i + 1) / (n_tr + 1)) for i in range(n_tr)]
        start = len(frames)
        frames += seg
        segments.append({"gloss": g, "start": start, "end": len(frames) - 1, "has_motion": has})
    return {"fps": fps, "frames": frames, "segments": segments}
