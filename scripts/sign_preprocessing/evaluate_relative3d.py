"""Body-relative 3D agreement with camera-frame AI Hub labels; never millimetres.
Both skeletons are independently centred/scaled on median clip shoulders.
No per-frame rigid/Procrustes fit, reference-assisted prediction, or hand swapping.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import imageio_ffmpeg

import aihub_converter as ah
from batch_v3 import video_info
from pipeline import Pipeline, sha, write

BODY = [2, 5, 3, 6, 4, 7]


def normalize_camera_skeleton(
    points: np.ndarray, valid: np.ndarray, flip_camera_axes: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Input BODY25+left21+right21; valid shoulders set the clip reference frame."""
    a = np.asarray(points, float).copy()
    v = np.asarray(valid, bool) & np.isfinite(a).all(2)
    if a.ndim != 3 or a.shape[1:] != (67, 3) or v.shape != a.shape[:2]:
        raise ValueError("3D skeleton shape mismatch")
    if flip_camera_axes:
        a *= [1, -1, -1]
    good = v[:, 2] & v[:, 5]
    widths = np.linalg.norm(a[:, 2] - a[:, 5], axis=1)
    good &= np.isfinite(widths) & (widths > 1e-6)
    if not good.any():
        raise ValueError("No valid 3D shoulders")
    center = np.median((a[good, 2] + a[good, 5]) / 2, axis=0)
    width = float(np.median(widths[good]))
    a = (a - center) / width
    return a, v


def score_relative3d(
    reference: np.ndarray,
    predicted: np.ndarray,
    reference_valid: np.ndarray,
    predicted_valid: np.ndarray,
) -> dict:
    """Use a fixed reference denominator, including unavailable predictions as PCK failures."""
    g = np.asarray(reference_valid, bool)
    a = np.asarray(predicted_valid, bool) & np.isfinite(predicted).all(2)
    e = np.linalg.norm(predicted - reference, axis=2)
    use = g & a
    values = e[use]
    n = int(g.sum())
    z = np.abs(predicted[:, :, 2] - reference[:, :, 2])[use]
    result = {
        "reference_points": n,
        "available_points": int(use.sum()),
        "missing_points": int((g & ~a).sum()),
        "coverage_pct": 100 * int(use.sum()) / n if n else None,
        "mean_distance_shoulder_units_available_only": float(values.mean())
        if len(values)
        else None,
        "median_distance_shoulder_units_available_only": float(np.median(values))
        if len(values)
        else None,
        "p95_distance_shoulder_units_available_only": float(np.quantile(values, 0.95))
        if len(values)
        else None,
        "mean_abs_relative_z_error_shoulder_units_available_only": float(z.mean())
        if len(z)
        else None,
    }
    for t in [0.1, 0.2, 0.5, 1.0]:
        result[f"pck3d_{t}_pct"] = 100 * int((use & (e <= t)).sum()) / n if n else None
    return result


def run(label_root: Path, video_root: Path, output: Path, chunk_frames: int = 150) -> Path:
    """Evaluate all frames from available 3D-labelled front videos in bounded-memory chunks."""
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / "input"
    inputs.mkdir(exist_ok=True)
    videos = {p.name: p for p in video_root.rglob("*.mp4")}
    cases = []
    sources = {}
    for path in sorted(label_root.rglob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        lm = d.get("landmarks", {})
        if "pose_keypoints_3d" not in lm:
            continue
        id = d["metadata"].get("id", path.stem)
        source = videos.get(id + "_F.mp4")
        if source is None:
            continue
        info = video_info(source)
        n = len(lm["pose_keypoints_3d"])
        if info["frames"] != n or abs(info["fps"] - float(d["metadata"]["video_fps"])) > 1e-6:
            raise ValueError(id + ": reference/video frame mismatch")
        projection = np.asarray(lm[("camera_parameter")][("Fprojectionmatrix")], float).reshape(
            3, 4
        )
        if (
            not np.allclose(projection[:, 3], 0)
            or not np.allclose(projection[2], [0, 0, 1, 0])
            or projection[0, 0] <= 0
            or projection[1, 1] <= 0
        ):
            raise ValueError(id + ": unsupported front-camera projection; do not assume axes")
        parts = [
            np.asarray(lm[k], float).reshape(n, count, 3)
            for k, count in [
                ("pose_keypoints_3d", 25),
                ("hand_left_keypoints_3d", 21),
                ("hand_right_keypoints_3d", 21),
            ]
        ]
        gt = np.concatenate(parts, axis=1)
        valid = np.isfinite(gt).all(2) & ~(gt == 0).all(2)
        sources[id] = {
            "label": path,
            "reference": gt,
            "valid": valid,
            "frames": n,
            "source_video": source.name,
        }
        for start in range(0, n, chunk_frames):
            end = min(n, start + chunk_frames)
            # Avoid a one-frame tail, preserving every frame exactly once.
            if end - start < 2:
                raise ValueError("Increase chunk size to avoid one-frame tail")
            name = f"3D_{id}_{start:05d}_{end:05d}"
            clip = inputs / (name + ".mp4")
            if not clip.exists():
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-vf",
                        f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
                        "-an",
                        "-c:v",
                        "libx264",
                        "-crf",
                        "0",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        str(clip),
                    ],
                    check=True,
                )
            if video_info(clip)["frames"] != end - start:
                raise ValueError("Clip frame mismatch")
            cases.append(
                {
                    "name": name,
                    "id": id,
                    "start": start,
                    "end": end,
                    "source_sha256": sha(source),
                    "label_sha256": sha(path),
                }
            )
    if not cases:
        raise ValueError("No matched 3D labels/videos")
    write(output / "input_manifest.json", cases)
    p = Pipeline(output / "pipeline")
    p.process(sorted(inputs.glob("*.mp4")))
    refs = []
    preds = []
    ref_masks = []
    pred_masks = []
    details = []
    index = BODY + list(range(25, 67))
    for case in cases:
        row = p.db["items"][case["name"]]
        if row["stage"] == "failed":
            raise ValueError("3D pipeline failed: " + case["name"])
        d = json.loads(p.pathof(row, "avatar").read_text())
        _, three, state = ah.ah_avatar_convert(d, case["name"])
        n = case["end"] - case["start"]
        g = sources[case["id"]]
        start = case["start"]
        end = case["end"]
        ref = g["reference"][start:end]
        rv = g["valid"][start:end]
        arrays = []
        masks = []
        for field, count in [
            ("pose_keypoints_3d", 25),
            ("hand_left_keypoints_3d", 21),
            ("hand_right_keypoints_3d", 21),
        ]:
            arrays.append(np.asarray(three["landmarks"][field]).reshape(n, count, 3))
            status = np.asarray(state["point_status"][field])
            masks.append((status > 0) & (status != 4))
        pred = np.concatenate(arrays, axis=1)
        pv = np.concatenate(masks, axis=1)
        # Independent clip shoulder origins/scales, with no fitting of predictions to reference.
        ref, rv = normalize_camera_skeleton(ref, rv, True)
        pred, pv = normalize_camera_skeleton(pred, pv, False)
        gate = rv[:, 2] & rv[:, 5]
        rv &= gate[:, None]
        ref = ref[:, index]
        pred = pred[:, index]
        rv = rv[:, index]
        pv = pv[:, index]
        refs.append(ref)
        preds.append(pred)
        ref_masks.append(rv)
        pred_masks.append(pv)
        details.append({**case, "scores": score_relative3d(ref, pred, rv, pv)})
    refs = np.concatenate(refs)
    preds = np.concatenate(preds)
    rv = np.concatenate(ref_masks)
    pv = np.concatenate(pred_masks)
    summary = {
        "source_videos": len(sources),
        "chunks": len(cases),
        "frames": len(refs),
        "signers": ["TW07"],
        ("scope"): (
            "3D body-relative shape agreement; not absolute camera position, millim"
            "etres, or translation comprehension"
        ),
        "unit": "independently normalized median 3D shoulder width per clip",
        "axes": "GT front-camera +X right,+Y down,+Z away -> project +X right,+Y up,+Z toward",
        ("axis_evidence"): (
            "Stored Fprojectionmatrix has [K|0], K focal lengths positive, third ro"
            "w [0,0,1,0]. Camera-axis interpretation inferred from this calibration"
            "."
        ),
        ("validity"): (
            "finite nonzero GT; finite, non-synthetic prediction; valid GT shoulder"
            "s; no 3D label confidence field"
        ),
        ("alignment"): (
            "independent clip centering/scaling only; no rigid/Procrustes fit, DTW or hand swap"
        ),
        "pipeline_settings": p.settings,
        "code_signature": p.db["signature"],
        "all": score_relative3d(refs, preds, rv, pv),
        "hands": score_relative3d(refs[:, 6:], preds[:, 6:], rv[:, 6:], pv[:, 6:]),
        "upper_body": score_relative3d(refs[:, :6], preds[:, :6], rv[:, :6], pv[:, :6]),
        "per_chunk": details,
    }
    write(output / "relative3d_accuracy.json", summary)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "per_chunk"}, ensure_ascii=False, indent=2
        ),
        flush=True,
    )
    return output / "relative3d_accuracy.json"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label-dir", type=Path, required=True)
    p.add_argument("--source-video-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    run(a.label_dir, a.source_video_dir, a.output_dir)


if __name__ == "__main__":
    main()
