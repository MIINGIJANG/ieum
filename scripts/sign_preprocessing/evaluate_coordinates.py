"""Compare same-video 2D reference joints with raw and processed predictions.
Missing predictions remain in the PCK denominator. No GT fitting or hand swapping.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from signal_geometry import inverse_xy


def nfc(s):
    return unicodedata.normalize("NFC", s)


def member_name(info):
    s = info.filename
    if not info.flag_bits & 0x800:
        try:
            s = s.encode("cp437").decode("utf-8")
        except (ValueError, UnicodeError):
            pass
    return nfc(s)


def word(s):
    s = nfc(PurePosixPath(s).stem)
    s = re.sub(r"^\d+[_ -]+", "", s)
    for _ in range(4):
        t = re.sub(r"(?i)[_ -](?:AIHub원본구간|landmarks|avatar|corrected)$", "", s)
        if t == s:
            break
        s = t
    return s


def load(path: str | Path, kind: str) -> tuple[dict, dict]:
    """Read paired reference/raw/avatar JSON from a directory or a ZIP."""
    path = Path(path)
    out = {}
    names = {}

    def consume(name: str, payload: bytes) -> None:
        p = PurePosixPath(nfc(name))
        if "__MACOSX" in p.parts or p.name.startswith(".") or p.suffix != ".json":
            return
        wanted = (
            (p.name == "AIHub_참고좌표_2D.json")
            if kind == "ref"
            else (
                (p.name == "landmarks.json" or p.name.endswith("_landmarks.json"))
                if kind == "raw"
                else p.name.endswith("_avatar.json")
            )
        )
        if not wanted:
            return
        d = json.loads(payload)
        w = (
            nfc(d["word"])
            if kind == "ref"
            else word(
                d.get("metadata", {}).get("source_file", p.parent.name)
                if kind == "raw"
                else d.get("name", p.name)
            )
        )
        if w in out:
            raise ValueError("중복 단어: " + w)
        out[w] = d
        names[w] = str(p)

    if path.is_dir():
        for p in sorted(path.rglob("*.json")):
            if "history" not in p.relative_to(path).parts:
                consume(p.relative_to(path).as_posix(), p.read_bytes())
    else:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if not info.is_dir():
                    consume(member_name(info), z.read(info))
    return out, names


def input_sha256(path: str | Path) -> str:
    """Hash a file or a reproducibly ordered directory without embedding local paths."""
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        entries = [("", path)]
    else:
        entries = sorted(
            (nfc(p.relative_to(path).as_posix()), p)
            for p in path.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
    for name, p in entries:
        if name:
            digest.update(name.encode() + b"\0")
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


POSE = [
    (2, 12, "right_shoulder"),
    (5, 11, "left_shoulder"),
    (3, 14, "right_elbow"),
    (6, 13, "left_elbow"),
    (4, 16, "right_wrist"),
    (7, 15, "left_wrist"),
]
PARTS = {
    "pose": slice(0, 6),
    "left_hand": slice(6, 27),
    "right_hand": slice(27, 48),
    "hands": slice(6, 48),
}


def safe_points(value, n):
    if value is None:
        return np.full((n, 3), np.nan)
    pts = []
    for p in value:
        pts.append(
            [np.nan, np.nan, np.nan] if p is None else [(np.nan if x is None else x) for x in p[:3]]
        )
    a = np.asarray(pts, float)
    if a.shape != (n, 3):
        raise ValueError(("point shape", a.shape, n))
    return a


def prepare(w, g, r, p, confidence):
    md = r["metadata"]
    rep = p["report"]
    T = g["n_frames"]
    fps = float(g["fps"])
    W = g["width"]
    H = g["height"]
    assert len(r["frames"]) == p["frames"] == T, (w, "frames")
    assert float(md["fps"]) == float(p["fps"]) == fps == float(rep["src_fps"]), (w, "fps")
    assert int(md["width"]) == W and int(md["height"]) == H, (w, "size")
    assert rep["trim_src_frames_inclusive"] == [0, T - 1], (w, "trim")
    assert md.get("mirrored_by_code") is False, (w, "mirror")
    assert word(md["source_file"]) == w, (w, "raw source name")
    assert word(p["name"]) == w, (w, "post source name")
    assert word(rep["input_coordinates"]) in [w, "landmarks"], (w, "post coordinate file")
    for i, f in enumerate(r["frames"]):
        assert f["frame"] == i, (w, "frame index", i)
        assert abs(float(f["timestamp_ms"]) - i * 1000 / fps) <= 0.501, (w, "timestamp", i)
    xy = np.full((T, 48, 2), np.nan)
    raw = xy.copy()
    post = xy.copy()
    conf = np.zeros((T, 48))
    synthetic = np.zeros((T, 48), bool)
    dup = []
    gp = np.asarray(g["landmarks"]["pose_keypoints_2d"], float).reshape(T, 25, 3)
    for j, (ai, mp, label) in enumerate(POSE):
        xy[:, j] = gp[:, ai, :2]
        conf[:, j] = gp[:, ai, 2]
        for t, f in enumerate(r["frames"]):
            pose = safe_points(f.get("pose"), 33)
            raw[t, j] = pose[mp, :2] * [W, H]
        post[:, j] = np.asarray(p["pose"], float)[:, mp, :2]
    for side, start, label in [("left_hand", 6, "Left"), ("right_hand", 27, "Right")]:
        gg = np.asarray(
            g[("landmarks")][
                ("hand_") + (("left") if label == ("Left") else ("right")) + ("_keypoints_2d")
            ],
            float,
        ).reshape(T, 21, 3)
        xy[:, start : start + 21] = gg[:, :, :2]
        conf[:, start : start + 21] = gg[:, :, 2]
        for t, f in enumerate(r["frames"]):
            hs = [h for h in f.get("hands", []) if h.get("label") == label]
            if len(hs) == 1:
                raw[t, start : start + 21] = safe_points(hs[0].get(("landmarks")), 21)[:, :2] * [
                    W,
                    H,
                ]
            elif len(hs) > 1:
                dup.append({"frame": t, "side": label, "count": len(hs)})
        post[:, start : start + 21] = np.asarray(p[side], float)[:, :, :2]
        sf = np.asarray(p.get("synthetic_rest_frames", {}).get(side, [False] * T), bool)
        synthetic[:, start : start + 21] |= sf[:, None]
        codes = np.asarray(p.get("point_source", {}).get(side, np.zeros((T, 21))), int)
        synthetic[:, start : start + 21] |= codes == 3
    # Invert the postprocessor's shoulder normalization; do not align predictions to reference.
    norm = rep["normalization"]
    unit = float(norm["shoulder_unit"])
    assert unit > 0 and math.isfinite(unit), (w, "norm")
    post = inverse_xy(post, norm)
    denom = np.linalg.norm(gp[:, 2, :2] - gp[:, 5, :2], axis=1)
    shoulders = (
        (gp[:, 2, 2] >= confidence)
        & (gp[:, 5, 2] >= confidence)
        & np.isfinite(denom)
        & (denom > 1e-6)
    )
    gate = np.isfinite(xy).all(2) & (conf >= confidence) & shoulders[:, None]
    # Synthetic fallback is not a measured hand prediction. Keep the same reference denominator.
    available_raw = np.isfinite(raw).all(2)
    available_post = np.isfinite(post).all(2) & ~synthetic
    er = np.linalg.norm(raw - xy, axis=2) / denom[:, None]
    ep = np.linalg.norm(post - xy, axis=2) / denom[:, None]
    # Numerically verify normalization inversion on points not interpolated/smoothed.
    common = available_raw & available_post
    movement = np.linalg.norm(post - raw, axis=2)
    return {
        "word": w,
        "T": T,
        "xy": xy,
        "raw": raw,
        "post": post,
        "gate": gate,
        "ar": available_raw,
        "ap": available_post,
        "er": er,
        "ep": ep,
        "synthetic": synthetic,
        "denom": denom,
        "shoulders": shoulders,
        "dup": dup,
        "movement": movement,
        "common": common,
        "report": rep,
        "source_id": g["provenance"]["source_id"],
        "clip_start_frame": g["provenance"]["original_start_frame_inclusive"],
        "confidence": conf,
    }


def stage_metrics(gate: np.ndarray, available: np.ndarray, errors: np.ndarray) -> dict:
    """Score a shared reference denominator; absent predictions count as PCK failures."""
    n = int(gate.sum())
    a = available
    e = errors
    use = gate & a
    v = e[use]
    return {
        "available_points": int(use.sum()),
        "missing_points": int((gate & ~a).sum()),
        "coverage_pct": 100 * int(use.sum()) / n if n else None,
        "pck_005_pct": 100 * int((use & (e <= 0.05)).sum()) / n if n else None,
        "pck_010_pct": 100 * int((use & (e <= 0.10)).sum()) / n if n else None,
        "pck_020_pct": 100 * int((use & (e <= 0.20)).sum()) / n if n else None,
        "pck_010_correct": int((use & (e <= 0.10)).sum()),
        "mean_normalized_error_detected": float(np.mean(v)) if len(v) else None,
        "median_normalized_error_detected": float(np.median(v)) if len(v) else None,
    }


def stats(rows, part):
    sl = PARTS[part]
    gate = np.concatenate([r["gate"][:, sl].reshape(-1) for r in rows])
    ar = np.concatenate([r["ar"][:, sl].reshape(-1) for r in rows])
    ap = np.concatenate([r["ap"][:, sl].reshape(-1) for r in rows])
    er = np.concatenate([r["er"][:, sl].reshape(-1) for r in rows])
    ep = np.concatenate([r["ep"][:, sl].reshape(-1) for r in rows])
    n = int(gate.sum())
    out = {"reference_valid_points": n, "reference_possible_points": len(gate)}
    out["raw"] = stage_metrics(gate, ar, er)
    out["post"] = stage_metrics(gate, ap, ep)
    common = gate & ar & ap
    for stage, e in [("raw", er), ("post", ep)]:
        out[stage]["mean_normalized_error_common_points"] = (
            float(np.mean(e[common])) if common.any() else None
        )
    out["common_points_count"] = int(common.sum())
    added = gate & ~ar & ap
    out["added_points"] = int(added.sum())
    out["added_points_correct_010"] = int((added & (ep <= 0.10)).sum())
    out["added_points_pck_010_pct"] = (
        100 * int((added & (ep <= 0.10)).sum()) / int(added.sum()) if added.any() else None
    )
    out["pck_010_improvement_pp"] = (
        out["post"]["pck_010_pct"] - out["raw"]["pck_010_pct"] if n else None
    )
    out["coverage_improvement_pp"] = (
        out["post"]["coverage_pct"] - out["raw"]["coverage_pct"] if n else None
    )
    before = n - out["raw"]["pck_010_correct"]
    after = n - out["post"]["pck_010_correct"]
    out["pck_010_failure_reduction_pct"] = 100 * (before - after) / before if before else None
    return out


def evaluate(ref_path, raw_path, post_path, confidence=0.5):
    refs, rnames = load(ref_path, "ref")
    raws, unames = load(raw_path, "raw")
    posts, pnames = load(post_path, "post")
    if not (set(refs) == set(raws) == set(posts)):
        raise ValueError(
            {"reference": len(refs), "raw": len(raws), "post": len(posts), "sets_differ": True}
        )
    rows = [prepare(w, refs[w], raws[w], posts[w], confidence) for w in sorted(refs)]
    syn = sum(int(r["synthetic"][:, 6::21].sum()) for r in rows)
    out = {
        "date": datetime.now(timezone(timedelta(hours=9))).date().isoformat(),
        "source_files": [nfc(Path(x).name) for x in [ref_path, raw_path, post_path]],
        "input_sha256": {
            nfc(Path(p).name): input_sha256(p) for p in [ref_path, raw_path, post_path]
        },
        "word_count": len(rows),
        "clip_frames": sum(r["T"] for r in rows),
        "fps": 30,
        "source_video_count": len(set(r["source_id"] for r in rows)),
        "settings": {
            "reference_confidence_min": confidence,
            "normalizer": "per-frame AI Hub shoulder distance BODY25 right=2,left=5",
            "pck_thresholds": [0.05, 0.10, 0.20],
            "joints": "hands 21x2; upper body 6",
            "missing_prediction": "PCK failure; remains in shared reference denominator",
            "left_right": "as provided; no best-fit swapping",
            ("alignment"): (
                "same frame; inverse postprocess normalization only; no spatial fit to GT or DTW"
            ),
            "synthetic": "excluded as prediction, retained reference denominator",
        },
        "aggregate": {s: stats(rows, s) for s in PARTS},
        "per_word": [],
        "processing_summary": {
            "synthetic_hand_frames": syn,
            "short_interpolated_hand_frames": sum(
                r["report"]["parts"][s]["interpolated_frames"]
                for r in rows
                for s in ["left_hand", "right_hand"]
            ),
            "synthetic_words": [r["word"] for r in rows if r["synthetic"].any()],
            "raw_duplicate_label_hand_frames": sum(len(r["dup"]) for r in rows),
            ("shoulder_reference_invalid_frames"): sum(
                int((~r[("shoulders")]).sum()) for r in rows
            ),
            "max_motion_existing_points_pixels": max(
                float(np.nanmax(r["movement"][r["common"]])) if r["common"].any() else 0
                for r in rows
            ),
            "smoothing_min_cutoff_values": sorted(
                set(r["report"]["smoothing"]["min_cutoff"] for r in rows)
            ),
            "max_gap_frames_values": sorted(set(r["report"]["max_gap_frames"] for r in rows)),
        },
    }
    # Clip-independent statistics are not claimed: count unique source frames and overlaps.
    unique = set()
    for r in rows:
        for i in range(r["T"]):
            unique.add((r["source_id"], r["clip_start_frame"] + i))
    out["unique_source_frames"] = len(unique)
    for r in rows:
        out["per_word"].append(
            {
                "word": r["word"],
                "frames": r["T"],
                "source_id": r["source_id"],
                "hands": stats([r], "hands"),
                "pose": stats([r], "pose"),
                "interpolated_left_hand_frames": r["report"]["parts"]["left_hand"][
                    "interpolated_frames"
                ],
                "interpolated_right_hand_frames": r["report"]["parts"]["right_hand"][
                    "interpolated_frames"
                ],
            }
        )
    # Macro average supplements point-weighted aggregate.
    for stage in ["raw", "post"]:
        vals = [
            r["hands"][stage]["pck_010_pct"]
            for r in out["per_word"]
            if r["hands"]["reference_valid_points"]
        ]
        out.setdefault("word_macro", {})[stage] = {"hands_pck_010_pct": float(np.mean(vals))}
    return out, rows


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--reference", required=True)
    a.add_argument("--raw", required=True)
    a.add_argument("--post", required=True)
    a.add_argument("--output", required=True)
    args = a.parse_args()
    out, rows = evaluate(args.reference, args.raw, args.post)
    # Confidence-threshold sensitivity; all choices are disclosed, not selected by best improvement.
    out["reference_confidence_sensitivity"] = {}
    for t in [0.3, 0.7]:
        other, _ = evaluate(args.reference, args.raw, args.post, t)
        out["reference_confidence_sensitivity"][str(t)] = other["aggregate"]["hands"]
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
    print(
        json.dumps(
            {
                k: out[k]
                for k in [
                    "word_count",
                    "clip_frames",
                    "unique_source_frames",
                    "processing_summary",
                    "aggregate",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
