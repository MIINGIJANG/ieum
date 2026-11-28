import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ksl_manager.interpolation import prepare_engine, reuse_extracted
from ksl_manager.service import sha256
from tools.postprocess_linear import interpolate_short


class BilateralInterpolationTests(unittest.TestCase):
    def test_both_hands_fill_independently_and_keep_observed_xyz(self):
        original = np.arange(20 * 42 * 3, dtype=float).reshape(20, 42, 3)
        original[3:6, :21] = np.nan
        original[3:8, 21:] = np.nan
        outputs = []
        for hand in [original[:, :21], original[:, 21:]]:
            result, filled = interpolate_short(hand, 5)
            self.assertTrue(np.isfinite(result).all())
            observed = np.isfinite(hand)
            np.testing.assert_array_equal(result[observed], hand[observed])
            self.assertTrue(filled.any())
            outputs.append(result)
        expected = outputs[0][2] * (1 - 1 / 4) + outputs[0][6] / 4
        np.testing.assert_allclose(outputs[0][3], expected)

    def test_edges_long_gaps_never_detected_and_zero_limit_stay_unknown(self):
        a = np.ones((20, 21, 3))
        a[:2] = np.nan
        a[4:12] = np.nan
        a[-2:] = np.nan
        result, filled = interpolate_short(a, 5)
        np.testing.assert_array_equal(np.isnan(result), np.isnan(a))
        self.assertFalse(filled.any())
        empty = np.full((20, 21, 3), np.nan)
        self.assertFalse(np.isfinite(interpolate_short(empty, 12)[0]).any())
        short = np.ones((5, 21, 3))
        short[2] = np.nan
        self.assertTrue(np.isnan(interpolate_short(short, 0)[0][2]).all())

    def test_twelve_frame_limit_fills_seven_and_ten_frame_gaps(self):
        left = np.ones((30, 21, 3))
        right = left.copy()
        left[5:15] = np.nan
        right[12:19] = np.nan
        self.assertEqual(int(interpolate_short(left, 12)[1].any(axis=(1, 2)).sum()), 10)
        self.assertEqual(int(interpolate_short(right, 12)[1].any(axis=(1, 2)).sum()), 7)
        self.assertTrue(np.isnan(interpolate_short(left, 5)[0][5:15]).all())

    def test_pipeline_engine_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "original"
            (root / "src").mkdir(parents=True)
            (root / "run.py").write_text('print("fixture")', encoding="utf-8")
            marker = "def process(self):\n    if not self.settings['make_check_video']:cmd.append('--no-video')\n"
            (root / "src/pipeline.py").write_text(marker, encoding="utf-8")
            (root / "src/postprocess_v3_fixed.py").write_text("ORIGINAL FIXTURE", encoding="utf-8")
            job = Path(tmp) / "job"
            job.mkdir()
            engine, digest = prepare_engine(root, job, 0.4)
            self.assertEqual(
                (root / "src/postprocess_v3_fixed.py").read_text(encoding="utf-8"),
                "ORIGINAL FIXTURE",
            )
            self.assertEqual((root / "src/pipeline.py").read_text(encoding="utf-8"), marker)
            self.assertIn(
                "--hand-max-gap-sec", (engine / "src/pipeline.py").read_text(encoding="utf-8")
            )
            self.assertEqual(len(digest), 64)
            with self.assertRaises(ValueError):
                prepare_engine(root, Path(tmp) / "bad", float("nan"))

    def test_reuses_raw_without_reusing_approval_or_processed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old/results/collection_job"
            (old / "items/id/raw").mkdir(parents=True)
            (old / "items/id/original").mkdir()
            (old / "items/id/raw/landmarks.json").write_text('{"fixture":true}', encoding="utf-8")
            (old / "items/id/raw/_normalized.mp4").write_bytes(b"synthetic")
            (old / "items/id/original/test.mp4").write_bytes(b"synthetic")
            row = {
                "raw": "items/id/raw/landmarks.json",
                "normalized_video": "items/id/raw/_normalized.mp4",
                "source": "items/id/original/test.mp4",
                "source_sha256": sha256(old / "items/id/original/test.mp4"),
                "stage": "approved",
                "approval": {"fixture": True},
                "history": [],
            }
            (old / "pipeline.json").write_text(
                json.dumps({"signature": "old", "items": {"test": row}}), encoding="utf-8"
            )
            reuse_extracted(Path(tmp) / "old", Path(tmp) / "new")
            result = json.loads(
                (Path(tmp) / "new/results/collection_job/pipeline.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("signature", result)
            self.assertEqual(result["items"]["test"]["stage"], "extracted")
            self.assertIsNone(result["items"]["test"]["approval"])
