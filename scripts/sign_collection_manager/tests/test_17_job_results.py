import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ksl_manager.job_results import result_items, review_bundle

APP = Path(__file__).resolve().parents[1] / "app.py"


def fixture_job(root):
    job = root / "jobs/test_job"
    folder = job / "results/collection_job/items/fixture"
    processed = folder / "processed"
    processed.mkdir(parents=True)
    (folder / "raw").mkdir()
    (processed / "fixture_avatar.json").write_text(
        json.dumps(
            {
                "frames": 30,
                "fps": 30,
                "coord_system": "shoulder; +X right,+Y up,+Z toward camera",
                "pose": [[[0, 0, 1]] * 33 for _ in range(30)],
                "left_hand": [[[0, 0, 1]] * 21 for _ in range(30)],
                "right_hand": [[[0, 0, 1]] * 21 for _ in range(30)],
            }
        ),
        encoding="utf-8",
    )
    (folder / "raw/_normalized.mp4").write_bytes(b"synthetic video fixture")
    (folder / "audit.json").write_text(
        json.dumps(
            {
                "frames": 30,
                "fps": 30,
                "events": [
                    {
                        "code": "long_internal_hand_gap",
                        "part": "left_hand",
                        "frame_ranges_end_exclusive": [[3, 9]],
                        "detail": "synthetic test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (job / "result.json").write_text('{"state":"완료","returncode":0}', encoding="utf-8")
    (job / "job.json").write_text('{"display_name":"합성시험"}', encoding="utf-8")
    return job


class JobResultsTests(unittest.TestCase):
    def test_bundle_and_review_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = result_items(fixture_job(Path(tmp)))[0]
            self.assertEqual(item["events"][0]["시작(초)"], 0.1)
            self.assertEqual(item["events"][0]["끝(초)"], 0.3)
            with zipfile.ZipFile(io.BytesIO(review_bundle(item, "시험"))) as z:
                self.assertIn("시험_avatar.json", z.namelist())
                self.assertIn("시험.mp4", z.namelist())
                self.assertNotIn("landmarks.json", z.namelist())

    def test_results_page_displays_downloads(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {"KSL_DB": str(Path(tmp) / "db"), "KSL_DATA_DIR": tmp, "OPENAI_API_KEY": ""},
            ),
        ):
            fixture_job(Path(tmp))
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            app.sidebar.radio[0].set_value("통계·작업").run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            labels = {b.proto.label for b in app.get("download_button")}
            self.assertIn("후처리 좌표 JSON 받기", labels)
            self.assertIn("수동 보정용 영상·좌표 ZIP 받기", labels)
