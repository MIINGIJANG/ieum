"""Depth controls must diagnose fixed predictions without promoting fitted scores."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnose_relative3d import depth_diagnostics, pearson


class DepthDiagnosticsTests(unittest.TestCase):
    def test_bias_correlation_and_input_preservation(self):
        ref = np.zeros((3, 2, 3))
        ref[:, :, 2] = np.arange(6).reshape(3, 2) / 5
        pred = ref.copy()
        pred[:, :, 2] += 2
        before = pred.copy()
        v = np.ones((3, 2), bool)
        d = depth_diagnostics(ref, pred, v, v)
        self.assertAlmostEqual(d["signed_mean_z_error_pred_minus_reference"], 2)
        self.assertAlmostEqual(d["pearson_z_pooled"], 1)
        self.assertAlmostEqual(d["spearman_z_pooled"], 1)
        self.assertAlmostEqual(d["gt_fitted_z_scale_diagnostic_only"], 1)
        self.assertAlmostEqual(d["gt_fitted_z_bias_diagnostic_only"], -2)
        np.testing.assert_array_equal(pred, before)

    def test_sign_flip_is_a_separate_control(self):
        ref = np.zeros((3, 2, 3))
        ref[:, :, 2] = np.arange(6).reshape(3, 2) / 5
        pred = ref.copy()
        pred[:, :, 2] *= -1
        v = np.ones((3, 2), bool)
        d = depth_diagnostics(ref, pred, v, v)
        self.assertAlmostEqual(d["pearson_z_pooled"], -1)
        self.assertEqual(
            d["diagnostic_controls"]["z_sign_flip"]["mean_distance_shoulder_units_available_only"],
            0,
        )
        self.assertGreater(d["pipeline"]["mean_distance_shoulder_units_available_only"], 0)

    def test_zero_depth_uses_same_mask(self):
        ref = np.zeros((2, 2, 3))
        pred = ref.copy()
        pred[:, :, 2] = 2
        rv = np.ones((2, 2), bool)
        pv = np.array([[True, False], [True, True]])
        d = depth_diagnostics(ref, pred, rv, pv)
        zero = d["diagnostic_controls"]["z_zero"]
        self.assertEqual(zero["mean_distance_shoulder_units_available_only"], 0)
        self.assertEqual(zero["missing_points"], d["pipeline"]["missing_points"])
        self.assertEqual(zero["coverage_pct"], d["pipeline"]["coverage_pct"])

    def test_constant_signal_has_no_correlation(self):
        self.assertIsNone(pearson(np.ones(5), np.arange(5.0)))

    def test_no_common_points_rejected(self):
        a = np.zeros((2, 2, 3))
        v = np.zeros((2, 2), bool)
        with self.assertRaises(ValueError):
            depth_diagnostics(a, a, v, v)
