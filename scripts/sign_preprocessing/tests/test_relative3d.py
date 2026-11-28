"""Analytic tests for the scope and denominator of relative-3D evaluation."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluate_relative3d import normalize_camera_skeleton, score_relative3d

BASE = Path(__file__).resolve().parents[1]


class Relative3DTests(unittest.TestCase):
    def test_axis_conversion_and_independent_normalization(self):
        a = np.ones((3, 67, 3))
        a[:, :, 2] = 5
        a[:, 2, 0] = -2
        a[:, 5, 0] = 2
        v = np.ones((3, 67), bool)
        gt, _ = normalize_camera_skeleton(a, v, True)
        pred, _ = normalize_camera_skeleton(a * [1, -1, -1] * 7 + [10, 20, 30], v, False)
        np.testing.assert_allclose(gt, pred, atol=1e-12)
        self.assertAlmostEqual(
            score_relative3d(gt, pred, v, v)["mean_distance_shoulder_units_available_only"], 0
        )

    def test_depth_error_is_not_fitted_away(self):
        gt = np.zeros((1, 2, 3))
        pred = gt.copy()
        pred[0, 0, 2] = 0.3
        v = np.ones((1, 2), bool)
        s = score_relative3d(gt, pred, v, v)
        self.assertAlmostEqual(s["mean_distance_shoulder_units_available_only"], 0.15)
        self.assertEqual(s["pck3d_0.2_pct"], 50)

    def test_missing_prediction_is_a_pck_failure(self):
        gt = np.zeros((1, 2, 3))
        v = np.ones((1, 2), bool)
        pv = np.array([[True, False]])
        s = score_relative3d(gt, gt.copy(), v, pv)
        self.assertEqual(s["reference_points"], 2)
        self.assertEqual(s["missing_points"], 1)
        self.assertEqual(s["pck3d_0.1_pct"], 50)

    def test_no_available_predictions_do_not_report_zero_error(self):
        a = np.zeros((1, 2, 3))
        v = np.ones((1, 2), bool)
        s = score_relative3d(a, a, v, ~v)
        self.assertIsNone(s["mean_distance_shoulder_units_available_only"])
        self.assertEqual(s["pck3d_0.1_pct"], 0)


if __name__ == "__main__":
    unittest.main()
