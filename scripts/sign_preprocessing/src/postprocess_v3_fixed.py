"""Mentor v3 rest-fill workflow, repaired for bounded missing-point interpolation and valid
filtering.
Input: matching video + MediaPipe raw/corrected JSON, or (T,543,3/4) NPY.
Model-relative Z is approximate; this does not reconstruct missing sign motion."""

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from signal_geometry import normalize_parts

PARTS = {
    "pose": (slice(0, 33), 33),
    "face": (slice(33, 501), 468),
    "left_hand": (slice(501, 522), 21),
    "right_hand": (slice(522, 543), 21),
}
HAND_EDGES = [
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
POSE_EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24)]


def runs(mask):
    out = []
    start = None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i))
            start = None
    return out


def interpolate_short(a: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """Only bounded gaps per coordinate; preserve all finite original components."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    out = a.copy()
    filled = np.zeros(a.shape, bool)
    for p in range(a.shape[1]):
        for c in range(3):
            for s, e in runs(~np.isfinite(a[:, p, c])):
                if s > 0 and e < len(a) and e - s <= limit:
                    out[s:e, p, c] = np.interp(
                        np.arange(s, e), [s - 1, e], [a[s - 1, p, c], a[e, p, c]]
                    )
                    filled[s:e, p, c] = True
    return out, filled


def fill_rest_gaps(hand, pose, limit, side, transition_frames=10):
    """V3-style display fallback, not sign recovery. Only whole-hand XY absence.
    Keep observed/manual points. Do not generate a never-observed hand.
    The mentor's generic 21-point template is retained for comparison only.
    """
    out = hand.copy()
    generated = np.zeros(len(hand), bool)
    weights = np.zeros(len(hand))
    events = []
    if not np.isfinite(hand).all(axis=(1, 2)).any():
        return out, generated, weights, events
    hip_idx = 23 if side == "left_hand" else 24
    absent = ~np.isfinite(hand[:, :, :2]).any(axis=(1, 2))
    for start, end in runs(absent):
        L = end - start
        if L <= limit:
            continue
        hips = pose[start:end, hip_idx]
        ok = np.isfinite(hips).all(1)
        template = np.full((L, 21, 3), np.nan)
        for i in np.where(ok)[0]:
            root = hips[i].copy()
            root[0] += 0.1 if side == "left_hand" else -0.1
            root[2] -= 0.1
            for j in range(21):
                template[i, j] = root + np.array([0.0, j * 0.02, j * 0.01])
        left = hand[start - 1] if start > 0 and np.isfinite(hand[start - 1]).all() else None
        right = hand[end] if end < len(hand) and np.isfinite(hand[end]).all() else None
        if left is not None and right is not None:
            alpha = (np.arange(L) + 1) / (L + 1)
            base = left[None] * (1 - alpha[:, None, None]) + right[None] * alpha[:, None, None]
        elif left is not None:
            base = np.broadcast_to(left, (L, 21, 3)).copy()
        elif right is not None:
            base = np.broadcast_to(right, (L, 21, 3)).copy()
        else:
            base = template.copy()
        k = min(max(0, int(transition_frames)), L // 2)
        blend = np.ones(L)
        if k:
            blend[:k] = np.arange(1, k + 1) / k
            blend[-k:] = np.arange(k, 0, -1) / k
        for i in np.where(ok)[0]:
            out[start + i] = base[i] * (1 - blend[i]) + template[i] * blend[i]
            generated[start + i] = True
            weights[start + i] = blend[i]
        events.append(
            {
                "start": start,
                "end_exclusive": end,
                "frames_generated": int(ok.sum()),
                "not_generated_missing_hip": int((~ok).sum()),
            }
        )
    return out, generated, weights, events


def smooth(a, fps, min_cutoff=1.0, beta=0.05):
    """1-Euro per coordinate; restart after a gap; never replace NaN by motion."""
    out = a.copy()
    if min_cutoff <= 0:
        return out
    for p in range(a.shape[1]):
        for c in range(3):
            prev = None
            dxprev = 0.0
            for t in range(len(a)):
                x = a[t, p, c]
                if not np.isfinite(x):
                    prev = None
                    dxprev = 0.0
                    continue
                if prev is None:
                    prev = x
                    continue
                dt = 1 / fps
                ad = 2 * math.pi * dt / (1 + 2 * math.pi * dt)
                dx = (x - prev) / dt
                dxhat = ad * dx + (1 - ad) * dxprev
                cutoff = min_cutoff + beta * abs(dxhat)
                v = 2 * math.pi * cutoff * dt
                alpha = v / (1 + v)
                prev = alpha * x + (1 - alpha) * prev
                dxprev = dxhat
                out[t, p, c] = prev
    return out


def resample(a, t_src, t_dst):
    """Interpolate only between consecutive valid input samples; keep long gaps."""
    out = np.full((len(t_dst),) + a.shape[1:], np.nan)
    for j, t in enumerate(t_dst):
        x = t / (t_src[1] - t_src[0]) if len(t_src) > 1 else 0.0
        left_index = min(int(math.floor(x + 1e-8)), len(a) - 1)
        r = min(left_index + 1, len(a) - 1)
        alpha = max(0, min(1, x - left_index))
        if alpha < 1e-7:
            out[j] = a[left_index]
        else:
            ok = np.isfinite(a[left_index]) & np.isfinite(a[r])
            out[j][ok] = a[left_index][ok] * (1 - alpha) + a[r][ok] * alpha
    return out


def load_coordinates(path):
    p = Path(path)
    notes = []
    if p.suffix.lower() == ".npy":
        a = np.load(p, allow_pickle=False)
        if a.ndim != 3 or a.shape[1] != 543 or a.shape[2] not in (3, 4):
            raise ValueError(
                (
                    "NPY는 (프레임,543,3 또는 4) MediaPipe 이미지 좌표여야 "
                    "합니다. AI Hub 좌표는 이 입력이 아닙니다."
                )
            )
        return np.asarray(a[:, :, :3], float), {}, notes
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    fs = d.get("frames")
    if not isinstance(fs, list):
        raise ValueError(
            (
                "frames가 리스트인 추출/수동보정 JSON을 넣으세요. *_ava"
                "tar.json이나 AI Hub 전달본은 입력할 수 없습니다."
            )
        )
    a = np.full((len(fs), 543, 3), np.nan)
    for i, f in enumerate(fs):
        for key in ["pose", "face"]:
            sl, n = PARTS[key]
            v = f.get(key)
            if v is not None:
                q = np.asarray([p if p is not None else [None, None, None] for p in v], float)
                if q.ndim == 2 and q.shape[0] >= n and q.shape[1] >= 3:
                    a[i, sl] = q[:n, :3]
        for key, label in [("left_hand", "Left"), ("right_hand", "Right")]:
            hs = [h for h in f.get("hands", []) if h.get("label") == label]
            if len(hs) > 1:
                notes.append(
                    {("frame"): i, ("side"): label, ("issue"): ("duplicate_label_not_selected")}
                )
                continue
            if len(hs) == 1:
                q = np.asarray(
                    [
                        p if p is not None else [None, None, None]
                        for p in hs[0].get("landmarks", [])
                    ],
                    float,
                )
                if q.shape == (21, 3):
                    a[i, PARTS[key][0]] = q
    return a, d.get("metadata", {}), notes


def clean(a):
    if a is None:
        return None
    if isinstance(a, np.ndarray):
        a = a.tolist()
    if isinstance(a, list):
        return [clean(v) for v in a]
    if isinstance(a, dict):
        return {k: clean(v) for k, v in a.items()}
    if isinstance(a, (float, np.floating)):
        return round(float(a), 6) if np.isfinite(a) else None
    if isinstance(a, np.integer):
        return int(a)
    return a


def draw(pose, face, lh, rh, W, H):
    img = np.full((H, W, 3), 30, np.uint8)
    scale = min(W / 3.4, H / 4)

    def point(v):
        return (
            (round(W / 2 + v[0] * scale), round(H * 0.3 - v[1] * scale))
            if np.isfinite(v[:2]).all()
            else None
        )

    for arr, edges, color in [
        (pose, POSE_EDGES, (235, 235, 235)),
        (face, [], (150, 150, 150)),
        (lh, HAND_EDGES, (255, 160, 60)),
        (rh, HAND_EDGES, (60, 200, 255)),
    ]:
        if arr is None:
            continue
        for left_index, r in edges:
            A, B = point(arr[left_index]), point(arr[r])
            if A is not None and B is not None:
                cv2.line(img, A, B, color, 2)
        for v in arr:
            q = point(v)
            if q is not None:
                cv2.circle(img, q, 1 if len(arr) > 33 else 2, color, -1)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("coordinates")
    ap.add_argument("--out", default="avatar_out")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--max-gap-sec", type=float, default=0.30)
    ap.add_argument("--trim", action="store_true")
    ap.add_argument("--pad", type=int, default=3)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--min-cutoff", type=float, default=0.0)
    ap.add_argument("--beta", type=float, default=0.05)
    ap.add_argument("--rest-mode", choices=["v3", "off"], default="v3")
    ap.add_argument("--rest-transition-frames", type=int, default=10)
    args = ap.parse_args()
    if args.fps <= 0 or args.max_gap_sec < 0 or args.pad < 0:
        raise ValueError("FPS, 누락 길이, 여유 프레임 설정을 확인하세요.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.video).stem
    kp, meta, notes = load_coordinates(args.coordinates)
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = []
    if not cap.isOpened() or fps <= 0:
        raise ValueError("영상 파일을 읽지 못했습니다.")
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    T0 = len(kp)
    if abs(len(frames) - T0) > 1:
        raise ValueError(
            (
                "영상 "
                f"{len(frames)}"
                "프레임 / 좌표 "
                f"{T0}"
                "프레임으로 다릅니다. 추출 당시 사용한 같은 FPS·크기의 "
                "영상을 넣으세요. 30fps로 변환해 추출했다면 해당 30fps "
                "영상이 필요합니다."
            )
        )
    if meta.get("fps") and abs(float(meta["fps"]) - fps) > 0.05:
        raise ValueError("영상과 JSON의 FPS가 다릅니다. 추출 당시 사용한 영상을 넣으세요.")
    if meta.get("width") and (int(meta["width"]) != W or int(meta["height"]) != H):
        raise ValueError("영상과 JSON의 크기가 다릅니다.")
    active = np.where(np.isfinite(kp[:, 501:, :2]).all(2).any(1))[0]
    if not len(active):
        raise ValueError("손 좌표가 없습니다. 추출 결과를 확인하세요.")
    s, e = (
        (max(0, int(active[0]) - args.pad), min(T0, int(active[-1]) + args.pad + 1))
        if args.trim
        else (0, T0)
    )
    kp = kp[s:e].copy()
    T = len(kp)
    limit = round(args.max_gap_sec * fps)
    parts = {}
    report = {
        "input_video": Path(args.video).name,
        "input_coordinates": Path(args.coordinates).name,
        "src_fps": fps,
        "src_frames": T0,
        "trim_src_frames_inclusive": [s, e - 1],
        "out_fps": args.fps,
        "max_gap_frames": limit,
        "parts": {},
        "duplicate_labels": notes,
        "metric_note": "보간 횟수·좌표 존재율이며 정답 대비 정확도가 아님",
        ("depth_note"): (
            "MediaPipe 이미지 상대 깊이를 손목·코에 근사 정렬. AI Hub 실측 3D 복원이 아님"
        ),
    }
    for name, (sl, n) in PARTS.items():
        a = kp[:, sl]
        before = np.isfinite(a).all(2)
        b, filled = interpolate_short(a, limit)
        parts[name] = b
        long = [list(g) for g in runs(~np.isfinite(b).all(axis=(1, 2)))]
        report["parts"][name] = {
            "raw_complete_point_ratio": float(before.mean()),
            "interpolated_frames": int(filled.any(axis=(1, 2)).sum()),
            "interpolated_components": int(filled.sum()),
            "remaining_missing_frame_runs_end_exclusive": long,
        }
    interpolated_masks = {
        k: (~np.isfinite(kp[:, PARTS[k][0]]).all(2)) & np.isfinite(a).all(2)
        for k, a in parts.items()
    }
    generated_src = {}
    weight_src = {}
    events = {}
    for name in ["left_hand", "right_hand"]:
        generated_src[name] = np.zeros(T, bool)
        weight_src[name] = np.zeros(T)
        events[name] = []
        if args.rest_mode == "v3":
            parts[name], generated_src[name], weight_src[name], events[name] = fill_rest_gaps(
                parts[name], parts["pose"], limit, name, args.rest_transition_frames
            )
    report["version"] = "mentor_v3_derived_fixed"
    report["synthetic_rest_src"] = events
    report["rest_mode"] = args.rest_mode
    report["rest_transition_frames"] = args.rest_transition_frames
    t_src = np.arange(T) / fps
    n_out = math.floor((T - 1) / fps * args.fps + 1e-8) + 1
    t_dst = np.arange(n_out) / args.fps
    parts = {k: resample(v, t_src, t_dst) for k, v in parts.items()}
    generated_out = {
        k: np.interp(t_dst, t_src, a.astype(float)) > 1e-8 for k, a in generated_src.items()
    }
    weight_out = {k: np.interp(t_dst, t_src, a) for k, a in weight_src.items()}
    parts, normalization = normalize_parts(parts, W, H)
    parts = {k: smooth(a, args.fps, args.min_cutoff, args.beta) for k, a in parts.items()}
    used = {k: bool(np.isfinite(parts[k]).all(2).any()) for k in ["left_hand", "right_hand"]}
    rest = {k: (~np.isfinite(parts[k]).all(2).all(1)).astype(float) for k in used}
    for k in rest:
        rest[k] = np.maximum(rest[k], weight_out[k])
    states = []
    for i in range(n_out):
        L = rest["left_hand"][i] == 0
        R = rest["right_hand"][i] == 0
        states.append(
            "synthetic_rest"
            if any(v[i] for v in generated_out.values())
            else "both"
            if L and R
            else "left_only"
            if L
            else "right_only"
            if R
            else "unobserved"
        )
    report["out_frames"] = n_out
    report["smoothing"] = {
        "algorithm": "1-Euro",
        "min_cutoff": args.min_cutoff,
        "beta": args.beta,
        "warning": "떨림 감소가 수어 정확도 개선을 뜻하지 않음",
    }
    report["normalization"] = normalization
    report["policy"] = (
        "V3 긴 손 누락에 가상 휴지 자세 생성. 관측 좌표·정답 복"
        "원이 아님. point_source=3을 검출률/정확도 개선으로 계"
        "산하지 말 것. 부분적으로 남아 있는 수동 좌표와 손이 전"
        "혀 검출된 적 없는 쪽은 가상 휴지 생성에서 제외."
    )
    report["synthetic_rest_out_frames"] = {
        k: np.where(v)[0].tolist() for k, v in generated_out.items()
    }
    source_codes = {}
    for k, a in parts.items():
        valid = np.isfinite(a).all(2)
        codes = np.where(valid, 1, 0).astype(int)
        src_index = np.clip(np.rint(t_dst * fps).astype(int), 0, T - 1)
        codes[interpolated_masks[k][src_index] & valid] = 2
        if k in generated_out:
            codes[generated_out[k] & valid.all(1), :] = 3
        source_codes[k] = codes.tolist()
    js = {
        "name": stem,
        "fps": args.fps,
        "frames": n_out,
        ("coord_system"): (
            "clip shoulder center; shoulder-width unit;+X right,+Y up,+Z toward cam"
            "era; approximate model relative depth"
        ),
        "hand_used": used,
        "hand_state": states,
        **parts,
        "left_hand_rest_weight": rest["left_hand"],
        "right_hand_rest_weight": rest["right_hand"],
        "point_valid": {k: np.isfinite(a).all(2).tolist() for k, a in parts.items()},
        "point_source_codes": {
            "0": "unknown",
            "1": "model coordinate; not ground truth",
            "2": "bounded short-gap interpolation",
            "3": "synthetic rest display fallback; not sign recovery",
        },
        "point_source": source_codes,
        "synthetic_rest_frames": {k: v.tolist() for k, v in generated_out.items()},
        "report": report,
    }
    (out / (stem + "_avatar.json")).write_text(
        json.dumps(clean(js), ensure_ascii=False, allow_nan=False)
    )
    (out / (stem + "_report.json")).write_text(
        json.dumps(clean(report), ensure_ascii=False, indent=2, allow_nan=False)
    )
    np.savez_compressed(
        out / (stem + "_avatar.npz"),
        **parts,
        fps=args.fps,
        **{k + "_rest_weight": v for k, v in rest.items()},
    )
    if not args.no_video:
        tmp = out / (stem + "_check_raw.mp4")
        writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*("mp4v")), args.fps, (W * 2, H))
        if not writer.isOpened():
            raise RuntimeError("검증 영상 저장기를 열지 못했습니다.")
        for i, t in enumerate(t_dst):
            j = min(round(t * fps) + s, len(frames) - 1)
            left = frames[j].copy()
            right = draw(
                *(parts[k][i] for k in [("pose"), ("face"), ("left_hand"), ("right_hand")]), W, H
            )
            cv2.putText(left, "original", (8, 24), 0, 0.6, (255, 255, 255), 1)
            cv2.putText(right, "v3 processed", (8, 24), 0, 0.6, (255, 255, 255), 1)
            if any(v[i] for v in generated_out.values()):
                cv2.putText(right, "SYNTHETIC REST", (8, 48), 0, 0.6, (0, 150, 255), 2)
            writer.write(np.hstack([left, right]))
        writer.release()
        ff = shutil.which("ffmpeg")
        if not ff:
            import imageio_ffmpeg

            ff = imageio_ffmpeg.get_ffmpeg_exe()
        final = out / (stem + "_check.mp4")
        subprocess.run(
            [
                ff,
                "-v",
                "error",
                "-y",
                "-i",
                str(tmp),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(final),
            ],
            check=True,
        )
        tmp.unlink()
    print(json.dumps(clean(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
