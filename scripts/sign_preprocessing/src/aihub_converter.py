from __future__ import annotations

import hashlib
import io
import json
import math
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping

# AI Hub 계열 연속 좌표 변환 함수 — 이 셀은 함수만 준비합니다.
from pathlib import Path
from typing import Any

import numpy as np

JSON = dict[str, Any]
Arrays = dict[str, np.ndarray]
PathLike = str | Path

AIHUB_BODY25 = [
    0,
    (11, 12),
    12,
    14,
    16,
    11,
    13,
    15,
    (23, 24),
    24,
    26,
    28,
    23,
    25,
    27,
    5,
    2,
    8,
    7,
    31,
    None,
    29,
    32,
    None,
    30,
]
AIHUB_FACE70 = [
    127,
    234,
    93,
    132,
    58,
    172,
    136,
    150,
    152,
    379,
    365,
    397,
    288,
    361,
    323,
    454,
    356,
    70,
    63,
    105,
    66,
    107,
    336,
    296,
    334,
    293,
    300,
    168,
    6,
    197,
    195,
    98,
    97,
    2,
    326,
    327,
    33,
    160,
    158,
    133,
    153,
    144,
    362,
    385,
    387,
    263,
    373,
    380,
    61,
    40,
    37,
    0,
    267,
    270,
    291,
    321,
    314,
    17,
    84,
    91,
    78,
    81,
    13,
    311,
    308,
    402,
    14,
    178,
    468,
    473,
]
AIHUB_KEYS = ["pose", "face", "hand_right", "hand_left"]
AIHUB_COUNTS = [25, 70, 21, 21]
AIHUB_HAND_EDGES = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (0, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    (0, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    (0, 17),
    (17, 18),
    (18, 19),
    (19, 20),
]
AIHUB_REST_BODY = np.array(
    [
        [0, 0.8, 0],
        [0, 0, 0],
        [-0.5, 0, 0],
        [-0.6, -0.65, 0],
        [-0.6, -1.25, 0],
        [0.5, 0, 0],
        [0.6, -0.65, 0],
        [0.6, -1.25, 0],
        [0, -1.4, 0],
        [-0.3, -1.4, 0],
        [-0.3, -2.5, 0],
        [-0.3, -3.6, 0],
        [0.3, -1.4, 0],
        [0.3, -2.5, 0],
        [0.3, -3.6, 0],
        [-0.1, 0.85, 0],
        [0.1, 0.85, 0],
        [-0.25, 0.75, 0],
        [0.25, 0.75, 0],
        [0.35, -3.7, 0.1],
        [0.45, -3.7, 0.1],
        [0.3, -3.6, -0.1],
        [-0.35, -3.7, 0.1],
        [-0.45, -3.7, 0.1],
        [-0.3, -3.6, -0.1],
    ],
    float,
)
AIHUB_REST_HAND = np.array(
    [
        [0, 0, 0],
        [-0.08, -0.04, 0],
        [-0.14, -0.1, 0],
        [-0.18, -0.16, 0],
        [-0.22, -0.21, 0],
        [-0.08, -0.18, 0],
        [-0.09, -0.32, 0],
        [-0.09, -0.42, 0],
        [-0.09, -0.5, 0],
        [0, -0.19, 0],
        [0, -0.35, 0],
        [0, -0.47, 0],
        [0, -0.55, 0],
        [0.07, -0.18, 0],
        [0.08, -0.33, 0],
        [0.08, -0.43, 0],
        [0.08, -0.51, 0],
        [0.12, -0.16, 0],
        [0.15, -0.28, 0],
        [0.16, -0.36, 0],
        [0.17, -0.43, 0],
    ],
    float,
)


def ah_write(path: PathLike, value: Any) -> None:
    """Write a JSON coordinate or provenance artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + ("\n"),
        encoding=("utf-8"),
    )


def ah_point_array(value: Any, count: int) -> np.ndarray:
    """Parse exactly count XYZ points, retaining missing values as NaN."""
    out = np.full((count, 3), np.nan)
    if value is None:
        return out
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"관절 수가 {count}개가 아닙니다.")
    for i, p in enumerate(value):
        if p is None:
            continue
        if not isinstance(p, list) or len(p) != 3:
            raise ValueError("관절은 [x,y,z] 또는 null이어야 합니다.")
        for j, v in enumerate(p):
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError("유효하지 않은 좌표값")
                out[i, j] = float(v)
    return out


def ah_validate_source(data: JSON) -> tuple[float, int, int, np.ndarray]:
    """Validate raw frames and require finite, positive video timing."""
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("metadata"), dict)
        or not isinstance(data.get("frames"), list)
        or not data["frames"]
    ):
        raise ValueError("MediaPipe metadata/frames 형식의 좌표 JSON이 아닙니다.")
    m = data["metadata"]
    fps = float(m["fps"])
    w = float(m["width"])
    h = float(m["height"])
    if not all(math.isfinite(x) and x > 0 for x in [fps, w, h]):
        raise ValueError("FPS·해상도가 올바르지 않습니다.")
    times = []
    for i, f in enumerate(data["frames"]):
        if f.get("frame") != i:
            raise ValueError("프레임 번호는 0부터 연속이어야 합니다.")
        t = f.get("timestamp_ms")
        if not isinstance(t, (int, float)) or not math.isfinite(t):
            raise ValueError("프레임 시각 누락")
        times.append(float(t) / 1000)
        ah_point_array(f.get("pose"), 33)
        ah_point_array(f.get("pose_world"), 33)
        if f.get("face") is not None and len(f["face"]) not in [468, 478]:
            raise ValueError("지원하지 않는 얼굴 관절 수")
        if not isinstance(f.get("hands", []), list):
            raise ValueError("hands가 목록이 아닙니다.")
        for hand in f.get("hands", []):
            if hand.get("label") not in ["Left", "Right"]:
                raise ValueError("알 수 없는 손 라벨")
            ah_point_array(hand.get("landmarks"), 21)
            ah_point_array(hand.get("world_landmarks"), 21)
    if len(times) > 1 and not np.all(np.diff(times) > 0):
        raise ValueError("시간축이 증가하지 않습니다.")
    if m.get("frame_count", len(times)) != len(times):
        raise ValueError("메타데이터 프레임 수가 실제 배열과 다릅니다.")
    return fps, w, h, np.array(times)


def ah_body25(a: np.ndarray) -> np.ndarray:
    """Map available MediaPipe body joints to BODY25 slots."""
    out = np.full((len(a), 25, 3), np.nan)
    for j, m in enumerate(AIHUB_BODY25):
        if m is not None:
            out[:, j] = a[:, list(m)].mean(1) if isinstance(m, tuple) else a[:, m]
    return out


def ah_wrapper(word: str, fps: float, n: int, source_id: str, landmarks: JSON) -> JSON:
    """Wrap coordinate sequences with timing and source identifiers."""
    return {
        "gloss": word,
        "fps": float(fps),
        "n_frames": n,
        "source_id": source_id,
        "source_start_time": 0.0,
        "source_end_time": n / fps,
        "source_start_frame": 0,
        "source_end_frame_exclusive": n,
        "landmarks": landmarks,
    }


def ah_short_gaps(
    a: np.ndarray, status: np.ndarray, times: np.ndarray, max_gap: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return copied arrays with bounded gaps filled and provenance marked."""
    out = a.copy()
    s = status.copy()
    for point in range(a.shape[1]):
        known = np.where(np.isfinite(a[:, point]).all(1))[0]
        for left, right in zip(known[:-1], known[1:]):
            if 0 < right - left - 1 <= max_gap:
                for t in range(left + 1, right):
                    alpha = (times[t] - times[left]) / (times[right] - times[left])
                    out[t, point] = a[left, point] * (1 - alpha) + a[right, point] * alpha
                    s[t, point] = 2
    return out, s


def ah_prepare_arrays(data: JSON) -> tuple[Any, ...]:
    """Collect raw pixel/world arrays and duplicate-hand candidates."""
    fps, w, h, times = ah_validate_source(data)
    fs = data["frames"]
    n = len(fs)
    pose = np.stack([ah_point_array(f.get("pose"), 33) for f in fs])
    world = np.stack([ah_point_array(f.get("pose_world"), 33) for f in fs])
    face = np.full((n, 70, 3), np.nan)
    right = np.full((n, 21, 3), np.nan)
    left = right.copy()
    wr = right.copy()
    wl = right.copy()
    duplicates = []
    origins = []
    for t, f in enumerate(fs):
        fm = f.get("face")
        if fm is not None:
            ff = ah_point_array(fm, len(fm))
            face[t] = [ff[j] if j < len(ff) else np.full(3, np.nan) for j in AIHUB_FACE70]
        for side, image, local in [("Right", right, wr), ("Left", left, wl)]:
            hands = [v for v in f.get("hands", []) if v["label"] == side]
            if len(hands) > 1:
                duplicates.append({"frame": t, "side": side, "candidates": hands})
                continue
            if len(hands) == 1:
                hand = hands[0]
                image[t] = ah_point_array(hand.get("landmarks"), 21)
                local[t] = ah_point_array(hand.get("world_landmarks"), 21)
                origins.append(
                    {
                        "frame": t,
                        "side": side,
                        "landmark_source": hand.get("landmark_source", "model"),
                        "assignment_source": hand.get("assignment_source", "model"),
                    }
                )
    return fps, w, h, times, pose, world, face, right, left, wr, wl, duplicates, origins


def _ah_convert_raw_v1(
    data: JSON, word: str, max_gap_frames: int = 5, nearest_depth_frames: int = 15
) -> tuple[JSON, JSON | None, JSON]:
    """Convert raw frames while tracking depth estimates and missing joints."""
    if not isinstance(max_gap_frames, int) or not 0 <= max_gap_frames <= 15:
        raise ValueError("보간 길이는 0~15프레임 정수로 지정하세요.")
    if not isinstance(nearest_depth_frames, int) or not 0 <= nearest_depth_frames <= 30:
        raise ValueError("깊이 참조 길이는 0~30프레임 정수로 지정하세요.")
    fps, w, h, times, pose, pw, face, right, left, wr, wl, duplicates, origins = ah_prepare_arrays(
        data
    )
    n = len(pose)
    source_name = str(data["metadata"].get("source_file", word))
    source_id = "mediapipe_derived:" + source_name
    # 2D companion, including native image missingness. Third field is availability, not
    # measured confidence.
    image_parts = [ah_body25(pose), face, right, left]
    lm2 = {}
    for name, a in zip(AIHUB_KEYS, image_parts):
        b = np.zeros_like(a)
        valid = np.isfinite(a[:, :, :2]).all(2)
        b[:, :, 0] = np.where(valid, a[:, :, 0] * w, 0)
        b[:, :, 1] = np.where(valid, a[:, :, 1] * h, 0)
        b[:, :, 2] = valid.astype(float)
        lm2[name + "_keypoints_2d"] = b.reshape(n, -1).tolist()
    out2 = ah_wrapper(word, fps, n, source_id, lm2)
    base_state = {
        "source_metadata": data["metadata"],
        "source_frame_times_seconds": times.tolist(),
        "duplicate_hand_candidates": duplicates,
        "hand_provenance": origins,
        ("face_mapping"): (
            "approximate FaceMesh-to-face70 selection, not official exact correspondence"
        ),
        "body_mapping": AIHUB_BODY25,
        "source_face70_indices": AIHUB_FACE70,
        "2d_confidence_semantics": "binary coordinate availability only; not model confidence",
        "source_fps_and_frames_preserved": True,
    }
    xy = pose[:, :, :2] * [w, h]
    good2 = np.isfinite(xy[:, [11, 12]]).all((1, 2))
    good3 = np.isfinite(pw[:, [11, 12]]).all((1, 2))
    width2 = np.linalg.norm(xy[:, 11] - xy[:, 12], axis=1)
    width3 = np.linalg.norm(pw[:, 11] - pw[:, 12], axis=1)
    good2 &= width2 > 1e-6
    good3 &= width3 > 1e-6
    if not good2.any() or not good3.any():
        base_state["3d_status"] = "not_generated_no_reliable_world_shoulders"
        return out2, None, base_state
    center2 = np.median(xy[good2][:, [11, 12]].mean(1), axis=0)
    center3 = np.median(pw[good3][:, [11, 12]].mean(1), axis=0)
    unit2 = float(np.median(width2[good2]))
    unit3 = float(np.median(width3[good3]))
    body = (pw - center3) / unit3 * [1, -1, -1]
    body[:, :, :2] = (xy - center2) / unit2 * [1, -1]
    body = ah_body25(body)
    face3 = face.copy()
    face3[:, :, :2] = (face[:, :, :2] * [w, h] - center2) / unit2 * [1, -1]
    for t, f in enumerate(data["frames"]):
        fm = f.get("face")
        if fm is not None:
            ff = ah_point_array(fm, len(fm))
            face3[t, :, 2] = body[t, 0, 2] - (face[t, :, 2] - ff[1, 2]) * w / unit2
    hand3 = []
    handstatus = []
    for image, local, wrist in [(right, wr, 4), (left, wl, 7)]:
        a = np.full((n, 21, 3), np.nan)
        a[:, :, :2] = (image[:, :, :2] * [w, h] - center2) / unit2 * [1, -1]
        outside = (
            (image[:, :, 0] < 0)
            | (image[:, :, 0] > 1)
            | (image[:, :, 1] < 0)
            | (image[:, :, 1] > 1)
        )
        a[outside, :2] = np.nan
        for t in range(n):
            if np.isfinite(local[t]).all():
                a[t, :, 2] = body[t, wrist, 2] - (local[t, :, 2] - local[t, 0, 2]) / unit3
            elif np.isfinite(image[t, :, 2]).any() and np.isfinite(image[t, 0, 2]):
                a[t, :, 2] = body[t, wrist, 2] - (image[t, :, 2] - image[t, 0, 2]) * w / unit2
            if np.isfinite(a[t, 0]).all():
                body[t, wrist] = a[t, 0]
        st = np.isfinite(a).all(2).astype(int)
        # Bounded temporal depth estimates for manually added XY points.
        relative = a[:, :, 2] - body[:, wrist, 2, None]
        for j in range(21):
            known = np.where(np.isfinite(relative[:, j]))[0]
            for left_index, r in zip(known[:-1], known[1:]):
                if r - left_index - 1 <= max_gap_frames:
                    for t in range(left_index + 1, r):
                        if np.isfinite(a[t, j, :2]).all() and not np.isfinite(a[t, j, 2]):
                            alpha = (times[t] - times[left_index]) / (times[r] - times[left_index])
                            a[t, j, 2] = (
                                body[t, wrist, 2]
                                + relative[left_index, j] * (1 - alpha)
                                + relative[r, j] * alpha
                            )
                            st[t, j] = 2
        anchors = np.where(np.isfinite(a).all((1, 2)))[0]
        for t in range(n):
            needed = np.isfinite(a[t, :, :2]).all(1) & ~np.isfinite(a[t, :, 2])
            if needed.any() and len(anchors):
                k = int(anchors[np.argmin(abs(anchors - t))])
                if abs(k - t) <= nearest_depth_frames:
                    a[t, needed, 2] = body[t, wrist, 2] + a[k, needed, 2] - body[k, wrist, 2]
                    st[t, needed] = 3
        hand3.append(a)
        handstatus.append(st)
    parts = [body, face3, *hand3]
    status = [
        np.isfinite(body).all(2).astype(int),
        np.isfinite(face3).all(2).astype(int),
        *handstatus,
    ]
    widths = np.linalg.norm(body[:, 2] - body[:, 5], axis=1)
    valid = np.isfinite(widths) & (widths > 1e-6)
    if not valid.any():
        base_state["3d_status"] = "not_generated_no_3d_scale"
        return out2, None, base_state
    unit = float(np.median(widths[valid]))
    center = np.median(body[valid][:, [2, 5]].mean(1), axis=0)
    parts = [(a - center) / unit for a in parts]
    rejected = {}
    for i, a in enumerate(parts):
        bad = (np.linalg.norm(a, axis=2) > 6) & np.isfinite(a).all(2)
        if i == 1:
            bad |= (np.linalg.norm(a - parts[0][:, 0, None, :], axis=2) > 1.3) & np.isfinite(a).all(
                2
            )
        if i in [2, 3]:
            wrist = 4 if i == 2 else 7
            badroot = np.linalg.norm(a[:, 0] - parts[0][:, wrist], axis=1) > 1.5
            bad |= badroot[:, None] & np.isfinite(a).all(2)
            bad |= (np.linalg.norm(a - a[:, 0, None, :], axis=2) > 1.3) & np.isfinite(a).all(2)
            for parent, child in AIHUB_HAND_EDGES:
                distance = np.linalg.norm(a[:, child] - a[:, parent], axis=1)
                bad[:, child] |= (distance > 0.5) & np.isfinite(distance)
        rejected[AIHUB_KEYS[i]] = int(bad.sum())
        a[bad] = np.nan
        status[i][bad] = 0
        parts[i], status[i] = ah_short_gaps(a, status[i], times, max_gap_frames)
    rest = {}
    active_unknown = {}
    for t in range(n):
        missing = ~np.isfinite(parts[0][t]).all(1)
        parts[0][t, missing] = AIHUB_REST_BODY[missing]
        status[0][t, missing] = 0
    for i, side, wrist in [(2, "Right", 4), (3, "Left", 7)]:
        weights = []
        for t in range(n):
            available = np.isfinite(parts[i][t]).all(1)
            weights.append(0.0 if int(available.sum()) >= 17 else 1.0)
            if available[0]:
                parts[i][t, available] += parts[0][t, wrist] - parts[i][t, 0]
            template = AIHUB_REST_HAND.copy()
            template[:, 0] *= 1 if side == "Left" else -1
            template += parts[0][t, wrist]
            parts[i][t, ~available] = template[~available]
            status[i][t, ~available] = 0
        rest[side] = weights
        active = (parts[0][:, wrist, 1] > parts[0][:, 8, 1] + 0.15) & (np.array(weights) >= 1)
        active_unknown[side] = np.where(active)[0].tolist()
    missing = ~np.isfinite(parts[1]).all(2)
    parts[1][missing] = 0
    status[1][missing] = 0
    lm3 = {
        name + "_keypoints_3d": np.round(a, 6).reshape(n, -1).tolist()
        for name, a in zip(AIHUB_KEYS, parts)
    }
    for a in parts:
        if not np.isfinite(a).all():
            raise ValueError("3D 결과에 비정상 좌표가 남았습니다.")
    if not np.allclose(parts[0][:, 4], parts[2][:, 0]) or not np.allclose(
        parts[0][:, 7], parts[3][:, 0]
    ):
        raise ValueError("손목 연결 검사 실패")
    state = {
        **base_state,
        "3d_status": "relative_model_estimate",
        ("coordinate_system"): (
            "clip shoulder midpoint origin; shoulder-width unit; +X image right,+Y "
            "up,+Z toward camera"
        ),
        "model_depth_is_not_ground_truth": True,
        "max_bounded_gap_frames": max_gap_frames,
        "max_nearest_depth_frames": nearest_depth_frames,
        "point_status_codes": {
            "0": "unobserved rest/neutral placeholder",
            "1": "model-supported relative3D estimate",
            "2": "bounded temporal interpolation",
            "3": "nearest model-depth estimate",
        },
        "point_status": {k + "_keypoints_3d": s.tolist() for k, s in zip(AIHUB_KEYS, status)},
        "hand_rest_weight": rest,
        "active_unknown_hand_frames": active_unknown,
        "face_enabled_frames": np.where((status[1] > 0).any(1))[0].tolist(),
        "geometric_outlier_points_rejected": rejected,
    }
    return out2, ah_wrapper(word, fps, n, source_id, lm3), state


# Postprocessed avatar support. Raw converter above remains unchanged.


def ah_is_avatar(d: Any) -> bool:
    """Recognize processed avatar objects without accepting booleans as frame counts."""
    return (
        isinstance(d, dict)
        and isinstance(d.get("frames"), int)
        and not isinstance(d.get("frames"), bool)
        and all(k in d for k in ["pose", "left_hand", "right_hand"])
    )


def ah_source_name(d: JSON, filename: str) -> str:
    """Derive a safe, Unicode-normalized word name from source metadata."""
    if ah_is_avatar(d):
        name = d.get("name") or d.get("report", {}).get("input_video") or filename
    else:
        name = d.get("metadata", {}).get("source_file", filename)
    name = Path(str(name).replace("\\", "/")).stem
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"_corrected(?:-\d+)?$", "", name)
    name = re.sub(r"_(?:avatar|landmarks)$", "", name)
    name = re.sub(r"_corrected(?:-\d+)?$", "", name)
    name = re.sub(r"^\d+_", "", name)
    name = re.sub(r"[\x00-\x1f/\\]", "_", name).strip(". ")
    return name or "word"


def ah_avatar_arrays(d: JSON) -> tuple[int, float, Arrays, Arrays, Arrays, list[str]]:
    """Validate avatar arrays and preserve validity/provenance distinctions."""
    n = d["frames"]
    fps = float(d["fps"])
    if n < 1 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("avatar 프레임 수·FPS 오류")
    coord = str(d.get("coord_system", ""))
    if not all(
        token in coord for token in [("shoulder"), ("+X right"), ("+Y up"), ("+Z toward camera")]
    ):
        raise ValueError(
            (
                "지원되지 않는 avatar 좌표계. 현재 v3의 어깨 기준 +X ri"
                "ght,+Y up,+Z toward camera 형식이 필요합니다."
            )
        )
    arrays = {}
    valid = {}
    sources = {}
    warnings = []
    codes = d.get("point_source_codes", {})
    for name, count in [("pose", 33), ("face", None), ("right_hand", 21), ("left_hand", 21)]:
        value = d.get(name)
        if name == "face" and value is None:
            value = [[None] * 468 for _ in range(n)]
        if not isinstance(value, list) or len(value) != n:
            raise ValueError(name + ": 프레임 수 불일치")
        if name == "face":
            count = len(value[0]) if isinstance(value[0], list) else 0
            if count not in [468, 478]:
                raise ValueError("얼굴은 468 또는 478개 관절이 필요합니다.")
        a = np.stack([ah_point_array(row, count) for row in value])
        finite = np.isfinite(a).all(2)
        supplied = d.get("point_valid", {}).get(name)
        if supplied is None:
            v = finite.copy()
            warnings.append(name + ": point_valid 없음; 유한 좌표로 가용도 추정")
        else:
            v = np.asarray(supplied)
            if v.shape != (n, count) or v.dtype.kind != "b":
                raise ValueError(name + ": point_valid 형식 오류")
            v = v & finite
        src = d.get("point_source", {}).get(name)
        if src is None:
            src = np.zeros((n, count), dtype=int)
            warnings.append(name + ": point_source 없음; 미확인 상태 0 유지")
        else:
            src = np.asarray(src)
            if src.shape != (n, count) or src.dtype.kind not in "iu" or np.any(src < 0):
                raise ValueError(name + ": point_source 형식 오류")
        status = np.zeros((n, count), dtype=int)
        for code in np.unique(src):
            desc = str(codes.get(str(int(code)), ""))
            lower = desc.lower()
            if code == 0:
                target = 0
            elif "synthetic" in lower or "rest display" in lower:
                target = 4
            elif "manual" in lower:
                target = 5
            elif "interpolation" in lower and code == 2:
                target = 2
            elif code == 1:
                target = 1
            else:
                # Unknown/custom provenance cannot be promoted to model observations.
                target = 0
                warnings.append(
                    name
                    + (": 출처 코드 ")
                    + str(code)
                    + ("를 미확인으로 처리; 원본 코드는 별도 보존")
                )
            status[(src == code) & v] = target
        arrays[name] = a
        valid[name] = v
        sources[name] = status
    return n, fps, arrays, valid, sources, warnings


def ah_avatar_convert(d: JSON, word: str) -> tuple[JSON | None, JSON, JSON]:
    """Map avatar joints without refitting XYZ or changing its timeline."""
    n, fps, arrays, valid, sources, warnings = ah_avatar_arrays(d)
    mapped = []
    masks = []
    states = []
    for name, indices in [
        ("pose", AIHUB_BODY25),
        ("face", AIHUB_FACE70),
        ("right_hand", list(range(21))),
        ("left_hand", list(range(21))),
    ]:
        a = arrays[name]
        v = valid[name]
        s = sources[name]
        out = np.zeros((n, len(indices), 3))
        vm = np.zeros((n, len(indices)), bool)
        st = np.zeros((n, len(indices)), int)
        for j, index in enumerate(indices):
            if index is None:
                continue
            ii = list(index) if isinstance(index, tuple) else [index]
            if any(k >= a.shape[1] for k in ii):
                continue
            # Average only defined synthetic body joints (neck/pelvis); never refit XYZ.
            finite = np.isfinite(a[:, ii]).all((1, 2))
            out[finite, j] = a[finite][:, ii].mean(1)
            vm[:, j] = v[:, ii].all(1) & finite
            code = s[:, ii[0]].copy()
            for k in ii[1:]:
                other = s[:, k]
                code = np.where(
                    (code == 0) | (other == 0),
                    0,
                    np.where((code == 4) | (other == 4), 4, np.maximum(code, other)),
                )
            st[:, j] = np.where(vm[:, j], code, 0)
        mapped.append(out)
        masks.append(vm)
        states.append(st)
    rest = {}
    synthetic = {}
    for name, side, idx in [("right_hand", "Right", 2), ("left_hand", "Left", 3)]:
        weights = np.asarray(d.get(name + "_rest_weight", [0.0] * n), dtype=float)
        if (
            weights.shape != (n,)
            or not np.isfinite(weights).all()
            or np.any((weights < 0) | (weights > 1))
        ):
            raise ValueError(name + ": 휴지 가중치 형식 오류")
        synth = np.asarray(d.get("synthetic_rest_frames", {}).get(name, [False] * n))
        if synth.shape != (n,) or synth.dtype.kind != "b":
            raise ValueError(name + ": synthetic_rest_frames 형식 오류")
        rest[side] = weights.tolist()
        synthetic[side] = synth.tolist()
        # A frame-level synthetic flag must not erase manual/observed points.
        # Frame flags and exact point provenance are both preserved for the loader.
    state = {
        "schema": "aihub_like_avatar_state_v2",
        "input_kind": "postprocessed_avatar",
        "3d_status": "postprocessed_relative_model_estimate",
        "coordinate_system": d["coord_system"],
        "model_depth_is_not_ground_truth": True,
        "fps_and_frame_count_preserved": True,
        ("xyz_policy"): (
            "existing XYZ preserved; body joint averages and approximate face selec"
            "tion only; no reinterpolation, depth inference, refit, or wrist snappi"
            "ng"
        ),
        "source_avatar_report": d.get("report", {}),
        "source_manual_correction_summary": d.get(
            "manual_correction_summary", d.get("manual_summary")
        ),
        "source_point_valid": d.get("point_valid"),
        "source_point_source": d.get("point_source"),
        "source_point_source_codes": d.get("point_source_codes"),
        "point_valid": {k + "_keypoints_3d": v.tolist() for k, v in zip(AIHUB_KEYS, masks)},
        "point_status": {k + "_keypoints_3d": s.tolist() for k, s in zip(AIHUB_KEYS, states)},
        "point_status_codes": {
            "0": "unknown/missing; not a measured sign point",
            "1": "model coordinate OR previous short interpolation; source cannot distinguish",
            "2": "bounded interpolation (raw converter only)",
            "3": "nearest depth estimate (raw converter only)",
            "4": "synthetic rest display fallback; not observed motion",
            "5": "manual coordinate with explicit provenance",
        },
        "hand_rest_weight": rest,
        "synthetic_rest_frames": synthetic,
        "source_hand_state": d.get("hand_state"),
        ("face_enabled_frames"): np.where(((states[1] > 0) & (states[1] != 4)).any(1))[0].tolist(),
        "body_mapping": AIHUB_BODY25,
        "source_face70_indices": AIHUB_FACE70,
        ("face_mapping"): (
            "approximate selection, NOT official exact correspondence; iris indices"
            " absent in face468 remain unknown"
        ),
        "duplicate_hand_candidates": d.get("report", {}).get("duplicate_labels", []),
        "conversion_warnings": warnings,
        "converter_did_add_interpolation": False,
        "converter_did_generate_depth": False,
        "active_unknown_hand_frames": {
            side: np.where((states[idx] == 0).any(1))[0].tolist()
            for side, idx in [("Right", 2), ("Left", 3)]
        },
    }
    source_id = "mediapipe_postprocessed:" + word
    lm3 = {k + "_keypoints_3d": a.reshape(n, -1).tolist() for k, a in zip(AIHUB_KEYS, mapped)}
    three = ah_wrapper(word, fps, n, source_id, lm3)
    two = None
    norm = d.get("report", {}).get("normalization", {})
    center = np.asarray(norm.get("center", []), dtype=float)
    unit = norm.get("shoulder_unit")
    if (
        center.shape in [(2,), (3,)]
        and np.isfinite(center).all()
        and isinstance(unit, (int, float))
        and math.isfinite(unit)
        and unit > 0
    ):
        lm2 = {}
        for key, a, vm, st in zip(AIHUB_KEYS, mapped, masks, states):
            b = np.zeros_like(a)
            ok = vm & (st > 0) & (st != 4)
            b[:, :, 0] = np.where(ok, a[:, :, 0] * unit + center[0], 0)
            b[:, :, 1] = np.where(ok, -a[:, :, 1] * unit + center[1], 0)
            b[:, :, 2] = ok.astype(float)
            lm2[key + "_keypoints_2d"] = b.reshape(n, -1).tolist()
        two = ah_wrapper(word, fps, n, source_id, lm2)
        state["2d_policy"] = (
            "inverse XY projection using saved normalization; synthetic/unknown poi"
            "nts have availability 0; not independently re-detected"
        )
    else:
        state["2d_policy"] = "not_generated_missing_inverse_projection_parameters"
    return two, three, state


def ah_convert(
    data: JSON, word: str, max_gap_frames: int = 5, nearest_depth_frames: int = 15
) -> tuple[JSON | None, JSON | None, JSON]:
    """Dispatch raw and processed-avatar inputs to their respective converters."""
    if ah_is_avatar(data):
        return ah_avatar_convert(data, word)
    return _ah_convert_raw_v1(data, word, max_gap_frames, nearest_depth_frames)


def ah_candidates(candidates: Iterable[tuple[str, JSON]]) -> list[tuple[str, JSON]]:
    """Select one unambiguous final coordinate object per source."""
    groups = {}
    for filename, d in candidates:
        avatar = ah_is_avatar(d)
        raw = (
            isinstance(d, dict)
            and isinstance(d.get("metadata"), dict)
            and isinstance(d.get("frames"), list)
        )
        if not (avatar or raw):
            continue
        if avatar:
            ah_avatar_arrays(d)
        else:
            ah_validate_source(d)
        key = ah_source_name(d, filename)
        edited = bool(
            d.get("manual_correction_summary")
            or d.get("manual_correction")
            or d.get("metadata", {}).get("manual_correction")
            or re.search(r"_corrected(?:-\d+)?\.json$", filename)
        )
        rank = (int(edited), int(avatar))
        sig = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rows = groups.setdefault(key, [])
        if not any(x[3] == sig for x in rows):
            rows.append((filename, d, rank, sig))
    selected = []
    for key, rows in groups.items():
        top = max(x[2] for x in rows)
        rows = [x for x in rows if x[2] == top]
        if len(rows) != 1:
            raise ValueError(
                key + (": 서로 다른 최종 후보가 여러 개입니다. 사용할 최종 JSON 하나만 선택하세요.")
            )
        selected.append((rows[0][0], rows[0][1]))
    if not selected:
        raise ValueError(
            (
                "변환 가능한 raw landmarks 또는 후처리 avatar JSON이 없"
                "습니다. 수정 기록/보고서만으로는 변환할 수 없습니다."
            )
        )
    return selected


def ah_collect_folder(folder: PathLike) -> list[tuple[str, JSON]]:
    """Collect eligible JSON inputs from a local directory."""
    rows = []
    for p in Path(folder).rglob("*.json"):
        if "__MACOSX" in p.parts or p.name.startswith("._"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            continue
        rows.append((p.name, d))
    return ah_candidates(rows)


def ah_collect_upload(uploaded: Mapping[str, bytes]) -> list[tuple[str, JSON]]:
    """Read JSON/ZIP uploads without extracting arbitrary archive paths."""
    rows = []
    total = 0
    for filename, blob in uploaded.items():
        if filename.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for m in z.infolist():
                    if (
                        m.is_dir()
                        or "__MACOSX" in m.filename.split("/")
                        or Path(m.filename).name.startswith("._")
                        or not m.filename.lower().endswith(".json")
                    ):
                        continue
                    total += m.file_size
                    if m.file_size > 128 * 1024**2 or total > 1024**3:
                        raise ValueError("ZIP 내 JSON 용량이 너무 큽니다. 나눠 업로드하세요.")
                    try:
                        d = json.loads(z.read(m).decode("utf-8-sig"))
                    except (ValueError, UnicodeError):
                        continue
                    rows.append((Path(m.filename).name, d))
        elif filename.lower().endswith(".json"):
            rows.append((Path(filename).name, json.loads(blob.decode("utf-8-sig"))))
        else:
            raise ValueError("JSON 여러 개 또는 결과 ZIP을 선택하세요.")
    return ah_candidates(rows)


def ah_export(
    candidates: Iterable[tuple[str, JSON]],
    output: PathLike,
    max_gap_frames: int = 5,
    nearest_depth_frames: int = 15,
) -> tuple[Path, list[JSON], list[JSON]]:
    """Export converted coordinates, playback state and an inspected ZIP."""
    output = Path(output)
    if output.exists():
        raise ValueError("이미 있는 출력 폴더입니다. 새 이름을 사용하세요.")
    selected = ah_candidates(candidates)
    output.mkdir(parents=True)
    items = []
    errors = []
    for filename, d in selected:
        word = ah_source_name(d, filename)
        try:
            two, three, state = ah_convert(d, word, max_gap_frames, nearest_depth_frames)
            f2 = f"AIHub_2D/{word}.json" if two else None
            f3 = f"AIHub_3D/{word}.json" if three else None
            sf = f"재생상태/{word}.json"
            if two:
                ah_write(output / f2, two)
            if three:
                ah_write(output / f3, three)
            ah_write(output / sf, state)
            ref = three or two
            items.append(
                {
                    "word": word,
                    "file_2d": f2,
                    "file_3d": f3,
                    "playback_state": sf,
                    "fps": ref["fps"],
                    "frames": ref["n_frames"],
                    ("input_kind"): ("postprocessed_avatar")
                    if ah_is_avatar(d)
                    else ("raw_landmarks"),
                    "3d_status": state["3d_status"],
                    "requires_motion_review": True,
                }
            )
            print(word, "→", ("2D + 상대3D" if two and three else "상대3D" if three else "2D"))
        except Exception as e:
            errors.append({"word": word, "file": filename, "error": str(e)})
            print("변환 실패:", word, str(e))
    ah_write(
        output / "자료목록.json",
        {
            "schema": "project AIHub-like wrapper; not official universal AIHub schema",
            "source_type": "MediaPipe-derived, not AIHub collected data",
            "items": items,
            "errors": errors,
        },
    )
    ah_write(
        output / "변환규칙.json",
        {
            "body25_mediapipe_indices": AIHUB_BODY25,
            "face70_approximate_indices": AIHUB_FACE70,
            "hand21_order_preserved": True,
            "status_file_required": True,
            "avatar_xyz_preserved": True,
            "avatar_gap_depth_settings": "not applied; previous postprocessing preserved",
        },
    )
    (output / "README.md").write_text(
        (
            (Path(__file__).resolve().parent.parent / ("tools") / ("변환결과_안내.md")).read_text(
                encoding=("utf-8")
            )
        ),
        encoding="utf-8",
    )
    if not items:
        raise ValueError(("변환 성공 파일이 없습니다. 생성된 자료목록.json의 errors를 확인하세요."))
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(output.rglob("*")):
            if p.is_file():
                z.write(p, Path(output.name, p.relative_to(output)).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("ZIP 검사 실패")
    return archive, items, errors


print("변환 함수 준비 완료: 원본/수동보정 raw JSON과 후처리 avatar JSON을 지원합니다.")
