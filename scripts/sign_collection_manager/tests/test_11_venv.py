import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ksl_manager.preprocessing import launch


class InterpreterTests(unittest.TestCase):
    def test_virtual_environment_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "pipeline"
            root.mkdir()
            (root / "run.py").write_text('print("fixture")', encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            video = Path(d) / "fixture.mp4"
            video.write_bytes(b"fixture")
            python = Path(d) / "python-link"
            python.symlink_to(sys.executable)
            job = launch(video, root, Path(d) / "work", python)
            for _ in range(50):
                if (job / "result.json").exists():
                    break
                time.sleep(0.05)
            command = json.loads((job / "job.json").read_text(encoding="utf-8"))["command"]
            self.assertEqual(command[0], str(python.absolute()))
