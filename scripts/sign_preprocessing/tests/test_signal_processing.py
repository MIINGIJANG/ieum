"""Signal-processing contracts with analytic inputs; no downloaded dataset required."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_local import avatar

import aihub_converter as converter
from evaluate_coordinates import stage_metrics
from model_integrity import ensure_model
from postprocess_v3_fixed import interpolate_short
from signal_geometry import inverse_xy, normalize_parts

BASE = Path(__file__).resolve().parents[1]


class InterpolationTests(unittest.TestCase):
    def test_bounded_gap_at_limit(self):
        a = np.array([0.0, np.nan, np.nan, 3.0])[:, None, None].repeat(3, axis=2)
        out, filled = interpolate_short(a, 2)
        np.testing.assert_allclose(out[:, 0, 0], [0, 1, 2, 3])
        self.assertEqual(filled.sum(), 6)
        self.assertTrue(np.isnan(a[1]).all())

    def test_gap_beyond_limit_stays_missing(self):
        a = np.array([0.0, np.nan, np.nan, np.nan, 4.0])[:, None, None].repeat(3, axis=2)
        out, filled = interpolate_short(a, 2)
        self.assertTrue(np.isnan(out[1:4]).all())
        self.assertFalse(filled.any())

    def test_leading_and_trailing_gaps_not_extrapolated(self):
        a = np.array([np.nan, 1.0, 2.0, np.nan])[:, None, None].repeat(3, axis=2)
        out, filled = interpolate_short(a, 5)
        self.assertTrue(np.isnan(out[[0, 3]]).all())
        self.assertFalse(filled.any())

    def test_finite_components_preserved(self):
        a = np.array([[[0.0, 0.0, 0.0]], [[5.0, np.nan, 7.0]], [[2.0, 2.0, 2.0]]])
        out, filled = interpolate_short(a, 1)
        np.testing.assert_allclose(out[1, 0], [5, 1, 7])
        self.assertEqual(filled.sum(), 1)

    def test_zero_limit_disables_fill(self):
        a = np.array([0.0, np.nan, 2.0])[:, None, None].repeat(3, axis=2)
        out, filled = interpolate_short(a, 0)
        self.assertTrue(np.isnan(out[1]).all())
        self.assertFalse(filled.any())

    def test_negative_limit_rejected(self):
        with self.assertRaises(ValueError):
            interpolate_short(np.ones((2, 1, 3)), -1)


class GeometryTests(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(23)
        parts = {
            k: rng.random((4, n, 3))
            for k, n in [("pose", 33), ("face", 468), ("left_hand", 21), ("right_hand", 21)]
        }
        parts["pose"][:, 11, :2] = [0.3, 0.4]
        parts["pose"][:, 12, :2] = [0.7, 0.4]
        return parts

    def test_normalization_xy_round_trip(self):
        parts = self.fixture()
        saved = {k: a.copy() for k, a in parts.items()}
        norm, meta = normalize_parts(parts, 800, 600)
        for k, a in parts.items():
            np.testing.assert_allclose(
                inverse_xy(norm[k], meta), saved[k][..., :2] * [800, 600], atol=1e-12
            )
            np.testing.assert_array_equal(a, saved[k])
        self.assertAlmostEqual(meta["shoulder_unit"], 320.0)

    def test_missing_shoulders_rejected(self):
        parts = self.fixture()
        parts["pose"][:, [11, 12]] = np.nan
        with self.assertRaises(ValueError):
            normalize_parts(parts, 800, 600)

    def test_invalid_inverse_scale_rejected(self):
        with self.assertRaises(ValueError):
            inverse_xy(np.ones((1, 2, 3)), {"center": [0, 0, 0], "shoulder_unit": 0})


class PCKTests(unittest.TestCase):
    def test_missing_predictions_stay_in_denominator(self):
        scores = stage_metrics(
            np.array([True, True, True, False]),
            np.array([True, True, False, True]),
            np.array([0.1, 0.2, np.nan, 0.0]),
        )
        self.assertEqual(scores["available_points"], 2)
        self.assertEqual(scores["missing_points"], 1)
        self.assertAlmostEqual(scores["pck_010_pct"], 100 / 3)
        self.assertAlmostEqual(scores["pck_020_pct"], 200 / 3)

    def test_threshold_is_inclusive(self):
        s = stage_metrics(np.ones(3, bool), np.ones(3, bool), np.array([0.05, 0.1, 0.2]))
        self.assertAlmostEqual(s["pck_005_pct"], 100 / 3)
        self.assertAlmostEqual(s["pck_010_pct"], 200 / 3)
        self.assertEqual(s["pck_020_pct"], 100)

    def test_empty_reference_no_fabricated_score(self):
        s = stage_metrics(np.zeros(2, bool), np.ones(2, bool), np.zeros(2))
        self.assertIsNone(s["pck_010_pct"])
        self.assertIsNone(s["coverage_pct"])


class MappingTests(unittest.TestCase):
    def test_body25_neck_pelvis_and_sides(self):
        d = avatar()
        p = np.asarray(d["pose"])
        p[:, :, 0] = np.arange(33)
        d["pose"] = p.tolist()
        _, three, state = converter.ah_avatar_convert(d, "안전")
        a = np.asarray(three["landmarks"]["pose_keypoints_3d"]).reshape(12, 25, 3)
        np.testing.assert_allclose(a[:, 1], p[:, [11, 12]].mean(1))
        np.testing.assert_allclose(a[:, 8], p[:, [23, 24]].mean(1))
        np.testing.assert_allclose(a[:, 2], p[:, 12])
        np.testing.assert_allclose(a[:, 5], p[:, 11])
        np.testing.assert_allclose(a[:, 4], p[:, 16])
        np.testing.assert_allclose(a[:, 7], p[:, 15])

    def test_unmapped_joints_are_unknown(self):
        _, three, state = converter.ah_avatar_convert(avatar(), "안전")
        pose = np.array(state["point_status"]["pose_keypoints_3d"])
        face = np.array(state["point_status"]["face_keypoints_3d"])
        self.assertTrue((pose[:, [20, 23]] == 0).all())
        self.assertTrue((face[:, [-2, -1]] == 0).all())

    def test_hand_xyz_order_is_preserved(self):
        d = avatar()
        a = np.asarray(d["left_hand"])
        a[:, :, 0] = np.arange(21)
        d["left_hand"] = a.tolist()
        _, three, _ = converter.ah_avatar_convert(d, "안전")
        actual = np.asarray(three["landmarks"]["hand_left_keypoints_3d"]).reshape(12, 21, 3)
        np.testing.assert_array_equal(actual, a)

    def test_synthetic_rest_not_model_observation(self):
        d = avatar()
        d["point_source"]["left_hand"] = [[3] * 21 for _ in range(12)]
        d["synthetic_rest_frames"]["left_hand"] = [True] * 12
        _, _, state = converter.ah_avatar_convert(d, "안전")
        self.assertTrue(
            (np.asarray(state[("point_status")][("hand_left_keypoints_3d")]) == 4).all()
        )


class ModelIntegrityTests(unittest.TestCase):
    def test_matching_cache_avoids_download(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model.task"
            p.write_bytes(b"model")

            def fail(*args):
                raise AssertionError("unexpected network")

            ensure_model(p, ("https://invalid.example"), hashlib.sha256(b"model").hexdigest(), fail)

    def test_altered_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model.task"
            p.write_bytes(b"altered")
            with self.assertRaises(ValueError):
                ensure_model(p, "https://invalid.example", "0" * 64)

    def test_bad_download_is_not_published(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model.task"

            def fetch(url, temp):
                temp.write_bytes(b"wrong")

            with self.assertRaises(ValueError):
                ensure_model(p, "https://invalid.example", "0" * 64, fetch)
            self.assertFalse(p.exists())
            self.assertFalse(p.with_suffix(".download").exists())


if __name__ == "__main__":
    unittest.main()
