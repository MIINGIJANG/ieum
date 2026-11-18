"""Shoulder normalization and image-plane inverse used by processing and evaluation."""

from typing import Any

import numpy as np


def normalize_parts(
    parts: dict[str, np.ndarray], width: int, height: int
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Align relative depths to pose anchors and scale using median 2D shoulder width.

    Input arrays are normalized image XYZ and are not mutated. Output axes are
    +X image right, +Y up and +Z toward camera, in shoulder-width units.
    """
    pixels = {
        k: np.asarray(a, dtype=float).copy() * [width, height, width] for k, a in parts.items()
    }
    pose = pixels["pose"]
    shoulders = pose[:, [11, 12]]
    good = np.isfinite(shoulders).all(axis=(1, 2))
    widths = np.linalg.norm(shoulders[:, 0, :2] - shoulders[:, 1, :2], axis=1)
    good &= widths > 1e-6
    if not good.any():
        raise ValueError("양쪽 어깨 좌표가 없어 정규화할 수 없습니다.")
    center = np.median(shoulders[good].mean(1), axis=0)
    unit = float(np.median(widths[good]))
    pixels["face"][:, :, 2] += pose[:, 0, 2, None] - pixels["face"][:, 1, 2, None]
    for name, wrist in [("left_hand", 15), ("right_hand", 16)]:
        pixels[name][:, :, 2] += pose[:, wrist, 2, None] - pixels[name][:, 0, 2, None]
    normalized = {k: (a - center) / unit * [1, -1, -1] for k, a in pixels.items()}
    return normalized, {"center": center.tolist(), "shoulder_unit": unit}


def inverse_xy(points: np.ndarray, normalization: dict[str, Any]) -> np.ndarray:
    """Invert XY only; depth anchor alignment is not a measured 3D reconstruction."""
    center = np.asarray(normalization["center"], dtype=float)
    unit = float(normalization["shoulder_unit"])
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(unit) or unit <= 0:
        raise ValueError("잘못된 정규화 정보")
    xy = np.asarray(points, dtype=float)[..., :2].copy()
    xy[..., 0] = xy[..., 0] * unit + center[0]
    xy[..., 1] = -xy[..., 1] * unit + center[1]
    return xy
