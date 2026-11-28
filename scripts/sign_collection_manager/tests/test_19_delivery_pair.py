import io
import tempfile
import unittest
import zipfile

from ksl_manager.delivery import delivery_archive, delivery_files
from tools.avatar_loader import load_clip


def avatar_fixture():
    return {
        "frames": 2,
        "fps": 30,
        "coord_system": "shoulder; +X right,+Y up,+Z toward camera",
        "pose": [[[0, 0, 1]] * 33 for _ in range(2)],
        "left_hand": [[[1, 2, 3]] * 21 for _ in range(2)],
        "right_hand": [[[2, 3, 4]] * 21 for _ in range(2)],
        "point_source_codes": {
            "1": "model coordinate",
            "2": "bounded interpolation",
            "3": "synthetic rest display",
            "4": "manual coordinate",
        },
        "point_source": {
            "pose": [[1] * 33 for _ in range(2)],
            "left_hand": [[2] * 21 for _ in range(2)],
            "right_hand": [[3] * 21, [4] * 21],
        },
        "right_hand_rest_weight": [1, 0],
        "synthetic_rest_frames": {"right_hand": [True, False], "left_hand": [False, False]},
    }


class DeliveryPairTests(unittest.TestCase):
    def test_same_frames_provenance_and_existing_loader(self):
        avatar = avatar_fixture()
        word, files = delivery_files(avatar, "시험")
        state = files["재생상태/시험.json"]
        three = files["좌표_3D/시험.json"]
        self.assertEqual(state["n_frames"], three["n_frames"])
        self.assertEqual(state["fps"], three["fps"])
        self.assertEqual(state["source_point_source"], avatar["point_source"])
        self.assertEqual(state["point_status"]["hand_left_keypoints_3d"][0], [2] * 21)
        self.assertEqual(state["point_status"]["hand_right_keypoints_3d"][0], [0] * 21)
        self.assertEqual(state["extended_point_status"]["hand_right_keypoints_3d"][0], [4] * 21)
        self.assertEqual(state["point_status"]["hand_right_keypoints_3d"][1], [1] * 21)
        self.assertEqual(state["extended_point_status"]["hand_right_keypoints_3d"][1], [5] * 21)
        self.assertFalse(state["human_review"]["approved"])
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(delivery_archive(files))) as z:
                z.extractall(tmp)
            loaded = load_clip(tmp, word)
            self.assertEqual(loaded["frames"], 2)
            self.assertTrue(loaded["requires_review"])

    def test_invalid_avatar_is_not_exported(self):
        with self.assertRaises((ValueError, KeyError)):
            delivery_files({"frames": 2, "fps": 30}, "시험")
