"""기존 프로젝트의 avatar 변환기를 분리. XYZ 재추정 없이 관절 구조를 대응한다."""

import math

import numpy as np

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


def ah_point_array(value, count):
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


def ah_wrapper(word, fps, n, source_id, landmarks):
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


def ah_avatar_arrays(d):
    n = d["frames"]
    fps = float(d["fps"])
    if n < 1 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("avatar 프레임 수·FPS 오류")
    coord = str(d.get("coord_system", ""))
    if not all(token in coord for token in ["shoulder", "+X right", "+Y up", "+Z toward camera"]):
        raise ValueError(
            "지원되지 않는 avatar 좌표계. 현재 v3의 어깨 기준 +X right,+Y up,+Z toward camera 형식이 필요합니다."
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
                    name + ": 출처 코드 " + str(code) + "를 미확인으로 처리; 원본 코드는 별도 보존"
                )
            status[(src == code) & v] = target
        arrays[name] = a
        valid[name] = v
        sources[name] = status
    return n, fps, arrays, valid, sources, warnings


def ah_avatar_convert(d, word):
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
        "xyz_policy": "existing XYZ preserved; body joint averages and approximate face selection only; no reinterpolation, depth inference, refit, or wrist snapping",
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
        "face_enabled_frames": np.where(((states[1] > 0) & (states[1] != 4)).any(1))[0].tolist(),
        "body_mapping": AIHUB_BODY25,
        "source_face70_indices": AIHUB_FACE70,
        "face_mapping": "approximate selection, NOT official exact correspondence; iris indices absent in face468 remain unknown",
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
            "inverse XY projection using saved normalization; synthetic/unknown points have availability 0; not independently re-detected"
        )
    else:
        state["2d_policy"] = "not_generated_missing_inverse_projection_parameters"
    return two, three, state
