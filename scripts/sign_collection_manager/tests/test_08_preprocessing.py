import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ksl_manager.preprocessing import job_status, launch


class PreprocessTests(unittest.TestCase):
    def test_isolated_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            root = p / "pipeline"
            root.mkdir()
            (root / "config.json").write_text('{"processing":{}}', encoding="utf-8")
            (root / "run.py").write_text(
                'print("synthetic runner; no pose extraction")', encoding="utf-8"
            )
            video = p / "test.mp4"
            video.write_bytes(b"fixture")
            job = launch(video, root, p / "work", sys.executable)
            for _ in range(50):
                if (job / "result.json").exists():
                    break
                time.sleep(0.1)
            self.assertEqual(job_status(job)["state"], "완료")
            cfg = json.loads((job / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(cfg["input_dir"].startswith(str((p / "work").resolve())))
            self.assertEqual(
                (root / "config.json").read_text(encoding="utf-8"), '{"processing":{}}'
            )
