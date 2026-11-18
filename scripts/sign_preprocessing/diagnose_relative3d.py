"""Diagnose depth conventions on fixed predictions; never alter primary evaluation.

Zero-depth/sign/scale/GT-fitted alternatives are diagnostic controls only.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import aihub_converter as converter
from evaluate_relative3d import BODY, normalize_camera_skeleton, score_relative3d
from pipeline import write


def pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    """Return no correlation when one signal is constant or has fewer than 3 pairs."""
    ok = np.isfinite(x) & np.isfinite(y)
    x = x[ok]
    y = y[ok]
    if len(x) < 3 or x.std() < 1e-10 or y.std() < 1e-10:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """Rank correlation retaining all finite pairs; ties receive average ranks."""
    ok = np.isfinite(x) & np.isfinite(y)

    def ranks(values: np.ndarray) -> np.ndarray:
        _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
        last = np.cumsum(counts)
        return (last - (counts + 1) / 2)[inverse]

    return pearson(ranks(x[ok]), ranks(y[ok]))


def depth_diagnostics(
    reference: np.ndarray,
    predicted: np.ndarray,
    reference_valid: np.ndarray,
    predicted_valid: np.ndarray,
) -> dict[str, Any]:
    """Use the same point mask for every counterfactual; do not promote any fitted score."""
    mask = (
        reference_valid
        & predicted_valid
        & np.isfinite(reference).all(2)
        & np.isfinite(predicted).all(2)
    )
    if not mask.any():
        raise ValueError("No common points for depth diagnostics")
    gz = reference[:, :, 2][mask]
    pz = predicted[:, :, 2][mask]
    dz = pz - gz
    x = np.column_stack([pz, np.ones_like(pz)])
    fitted = np.linalg.lstsq(x, gz, rcond=None)[0] if len(gz) > 1 else np.array([np.nan, np.nan])
    alternatives = {}
    for label, scale, bias in [
        ("z_zero", 0.0, 0.0),
        ("z_sign_flip", -1.0, 0.0),
        ("z_scale_0.1", 0.1, 0.0),
        ("z_scale_0.25", 0.25, 0.0),
        ("z_scale_0.5", 0.5, 0.0),
        ("z_scale_2", 2.0, 0.0),
        ("gt_fitted_affine_diagnostic_only", float(fitted[0]), float(fitted[1])),
    ]:
        a = predicted.copy()
        a[:, :, 2] = predicted[:, :, 2] * scale + bias
        alternatives[label] = score_relative3d(reference, a, reference_valid, predicted_valid)
    per_joint = []
    for j in range(reference.shape[1]):
        ok = mask[:, j]
        per_joint.append(
            {
                "index": j,
                "pairs": int(ok.sum()),
                "pearson_z": pearson(predicted[ok, j, 2], reference[ok, j, 2]),
            }
        )
    return {
        "pairs": len(gz),
        "signed_mean_z_error_pred_minus_reference": float(dz.mean()),
        "median_signed_z_error": float(np.median(dz)),
        "mean_absolute_z_error": float(np.abs(dz).mean()),
        "std_z_error": float(dz.std()),
        "pearson_z_pooled": pearson(pz, gz),
        "spearman_z_pooled": spearman(pz, gz),
        "reference_abs_z_over_10_shoulder_units": int((np.abs(gz) > 10).sum()),
        "prediction_z_mean": float(pz.mean()),
        "reference_z_mean": float(gz.mean()),
        "prediction_z_std": float(pz.std()),
        "reference_z_quantiles": dict(
            zip(
                ["min", "p01", "p25", "median", "p75", "p99", "max"],
                np.quantile(gz, [0, 0.01, 0.25, 0.5, 0.75, 0.99, 1]).tolist(),
            )
        ),
        "prediction_z_quantiles": dict(
            zip(
                ["min", "p01", "p25", "median", "p75", "p99", "max"],
                np.quantile(pz, [0, 0.01, 0.25, 0.5, 0.75, 0.99, 1]).tolist(),
            )
        ),
        "reference_z_std": float(gz.std()),
        "mae_xyz": np.abs(predicted - reference)[mask].mean(0).tolist(),
        "gt_fitted_z_scale_diagnostic_only": float(fitted[0]),
        "gt_fitted_z_bias_diagnostic_only": float(fitted[1]),
        "pipeline": score_relative3d(reference, predicted, reference_valid, predicted_valid),
        "diagnostic_controls": alternatives,
        "per_joint": per_joint,
        ("scope"): (
            "Same fixed masks and fixed XY. GT-fitted depth uses evaluation labels;"
            " not a valid held-out score or a deployed correction."
        ),
    }


def run(result_dir: Path, label_dir: Path, output: Path) -> dict[str, Any]:
    """Read saved avatar predictions and original labels; inference is not rerun or retuned."""
    cases = json.loads((result_dir / "input_manifest.json").read_text())
    labels = {}
    for p in label_dir.rglob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        if "pose_keypoints_3d" in d.get("landmarks", {}):
            labels[d["metadata"].get("id", p.stem)] = d
    refs = []
    preds = []
    rvs = []
    pvs = []
    source_ids = []
    within_correlations = []
    for case in cases:
        d = labels[case["id"]]
        s = case["start"]
        e = case["end"]
        n = e - s
        ref = np.concatenate(
            [
                np.asarray(d["landmarks"][k], float).reshape(-1, c, 3)[s:e]
                for k, c in [
                    ("pose_keypoints_3d", 25),
                    ("hand_left_keypoints_3d", 21),
                    ("hand_right_keypoints_3d", 21),
                ]
            ],
            axis=1,
        )
        rv = np.isfinite(ref).all(2) & ~(ref == 0).all(2)
        avatar = result_dir / "후처리좌표" / case["name"] / (case["name"] + "_avatar.json")
        _, three, state = converter.ah_avatar_convert(
            json.loads(avatar.read_text()), case[("name")]
        )
        pred = []
        pv = []
        for key, c in [
            ("pose_keypoints_3d", 25),
            ("hand_left_keypoints_3d", 21),
            ("hand_right_keypoints_3d", 21),
        ]:
            pred.append(np.asarray(three["landmarks"][key]).reshape(n, c, 3))
            status = np.asarray(state["point_status"][key])
            pv.append((status > 0) & (status != 4))
        pred = np.concatenate(pred, axis=1)
        pv = np.concatenate(pv, axis=1)
        ref, rv = normalize_camera_skeleton(ref, rv, True)
        pred, pv = normalize_camera_skeleton(pred, pv, False)
        rv &= (rv[:, 2] & rv[:, 5])[:, None]
        index = BODY + list(range(25, 67))
        ref = ref[:, index]
        pred = pred[:, index]
        rv = rv[:, index]
        pv = pv[:, index]
        for j in range(6, 48):
            mask = rv[:, j] & pv[:, j]
            corr = pearson(pred[mask, j, 2], ref[mask, j, 2])
            if corr is not None:
                within_correlations.append(corr)
        refs.append(ref)
        preds.append(pred)
        rvs.append(rv)
        pvs.append(pv)
        source_ids.extend([case["id"]] * n)
    ref = np.concatenate(refs)
    pred = np.concatenate(preds)
    rv = np.concatenate(rvs)
    pv = np.concatenate(pvs)
    source_ids = np.asarray(source_ids)
    summary = {
        ("scope"): (
            "Diagnostic-only analysis of existing 3D evaluation; primary prediction"
            "s and score unchanged"
        ),
        "units": "shoulder width; no conversion to metres or millimetres",
        "all": depth_diagnostics(ref, pred, rv, pv),
        "hands": depth_diagnostics(ref[:, 6:], pred[:, 6:], rv[:, 6:], pv[:, 6:]),
        "upper_body": depth_diagnostics(ref[:, :6], pred[:, :6], rv[:, :6], pv[:, :6]),
        "per_source_hands": {},
        "within_chunk_joint_z_correlation_median": float(np.median(within_correlations)),
    }
    for source in sorted(set(source_ids)):
        m = source_ids == source
        summary["per_source_hands"][source] = depth_diagnostics(
            ref[m, 6:], pred[m, 6:], rv[m, 6:], pv[m, 6:]
        )
    output.mkdir(parents=True, exist_ok=True)
    write(output / "relative3d_diagnostics.json", summary)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary["hands"].items()
                if k not in ["per_joint", "diagnostic_controls"]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("ZERO Z", summary["hands"]["diagnostic_controls"]["z_zero"])
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result-dir", type=Path, required=True)
    p.add_argument("--label-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    run(a.result_dir, a.label_dir, a.output_dir)


if __name__ == "__main__":
    main()
