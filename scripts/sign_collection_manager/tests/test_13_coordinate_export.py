import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ksl_manager.coordinate_export import CoordinateUnavailable, build_3d, locate_3d


class CoordinateExportTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "record_key": "SYNTHETIC_SOURCE:0",
            "gloss": "시험1",
            "start": 2.0,
            "end": 2.2,
            "reliable": 1,
            "dataset": "합성 시험",
            "split": "Training",
        }
        self.points = {
            "pose_keypoints_3d": [[i, 1, 2] for i in range(40)],
            "hand_right_keypoints_3d": [[i, 2, 3] for i in range(40)],
        }
        self.raw = {
            "metadata": {"id": "SYNTHETIC_SOURCE", "video_fps": "10.0"},
            "landmarks": self.points,
        }

    def test_original_3d_is_cropped_without_flattening_depth(self):
        d = build_3d(self.row, self.raw, "fixture.json")
        self.assertEqual(d["landmarks"]["pose_keypoints_3d"], [[20, 1, 2], [21, 1, 2]])
        self.assertEqual(d["metadata"]["frame_count"], 2)
        self.assertEqual(d["metadata"]["coordinate_type"], "original_3d")

    def test_2d_wrong_identity_missing_and_flat_depth_rejected(self):
        cases = [
            {
                "metadata": {"id": "SYNTHETIC_SOURCE", "video_fps": 10},
                "landmarks": {"pose_keypoints_2d": [[1, 2, 0.9]] * 40},
            },
            {"metadata": {"id": "OTHER", "video_fps": 10}, "landmarks": self.points},
            {
                "metadata": {"id": "SYNTHETIC_SOURCE", "video_fps": 10},
                "landmarks": {"pose_keypoints_3d": [[1, 2, 0]] * 40},
            },
        ]
        for data in cases:
            with self.assertRaises(CoordinateUnavailable):
                build_3d(self.row, data, "fixture")
        with self.assertRaises(CoordinateUnavailable):
            build_3d({**self.row, "start": None}, self.raw, "fixture")

    def test_clip_padding_removed_but_missing_gloss_not_invented(self):
        data = {
            "id": "SYNTHETIC_SOURCE",
            "fps": 10,
            "clips": [
                {
                    "gloss": "시험1",
                    "frame_start": 18,
                    "landmarks": {k: v[18:24] for k, v in self.points.items()},
                }
            ],
        }
        self.assertEqual(
            build_3d(self.row, data, "fixture")["landmarks"]["pose_keypoints_3d"],
            [[20, 1, 2], [21, 1, 2]],
        )
        with self.assertRaises(CoordinateUnavailable):
            build_3d({**self.row, "gloss": "없는단어"}, data, "fixture")

    def test_archive_member_is_read_without_unpacking_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "motions.tar.gz"
            payload = json.dumps(self.raw).encode()
            with tarfile.open(archive, "w:gz") as t:
                info = tarfile.TarInfo("motions/SYNTHETIC_SOURCE.json")
                info.size = len(payload)
                t.addfile(info, io.BytesIO(payload))
            result = locate_3d(self.row, [root])
            self.assertEqual(result["metadata"]["frame_count"], 2)
            self.assertEqual(list(root.iterdir()), [archive])


class TwoDFallbackTests(unittest.TestCase):
    def test_2d_confidence_is_preserved_and_labeled(self):
        from ksl_manager.coordinate_export import locate_coordinates, safe_filename

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = {
                "record_key": "FIXTURE:0",
                "gloss": "시험",
                "start": 0,
                "end": 0.2,
                "reliable": 1,
            }
            data = {
                "metadata": {"id": "FIXTURE", "video_fps": 10},
                "landmarks": {"pose_keypoints_2d": [[10, 20, 0.9], [11, 21, 0.8]]},
            }
            (root / "FIXTURE.json").write_text(json.dumps(data), encoding="utf-8")
            result = locate_coordinates(row, [root])
            self.assertEqual(result["metadata"]["coordinate_type"], "original_2d")
            self.assertFalse(result["metadata"]["depth_available"])
            self.assertEqual(result["landmarks"], data["landmarks"])
            self.assertEqual(safe_filename("시험", "2d"), "시험_2D.json")

    def test_later_archive_3d_wins_over_local_2d(self):
        from ksl_manager.coordinate_export import locate_coordinates

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = {
                "record_key": "FIXTURE:0",
                "gloss": "시험",
                "start": 0,
                "end": 0.2,
                "reliable": 1,
            }
            two = {
                "metadata": {"id": "FIXTURE", "video_fps": 10},
                "landmarks": {"pose_keypoints_2d": [[1, 2, 0.9], [2, 3, 0.8]]},
            }
            three = {
                "metadata": {"id": "FIXTURE", "video_fps": 10},
                "landmarks": {"pose_keypoints_3d": [[1, 2, 3], [2, 3, 4]]},
            }
            (root / "FIXTURE.json").write_text(json.dumps(two), encoding="utf-8")
            payload = json.dumps(three).encode()
            with tarfile.open(root / "motions.tar.gz", "w:gz") as tar:
                member = tarfile.TarInfo("motions/FIXTURE.json")
                member.size = len(payload)
                tar.addfile(member, io.BytesIO(payload))
            result = locate_coordinates(row, [root])
            self.assertEqual(result["metadata"]["coordinate_type"], "original_3d")
            self.assertEqual(result["landmarks"], three["landmarks"])
