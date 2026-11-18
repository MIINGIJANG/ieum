"""No models, API keys, or source videos needed. Run: python -m unittest discover -s tests -v"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np

from local_runtime import BusyError, Runtime, StableFiles, exclusive_lock
from local_web import ReviewServer
from pipeline import Pipeline, load, write

BASE = Path(__file__).resolve().parents[1]


def config(root):
    p = root / "config.json"
    write(
        p,
        {
            "input_dir": "incoming",
            "workspace_dir": "work",
            "run_name": "test",
            "stable_seconds": 1,
            "poll_seconds": 0.2,
            "processing": {"make_check_video": False},
            "ai": {"enabled": False},
        },
    )
    return p


def avatar(name="안전", n=12):
    a = {
        k: np.ones((n, count, 3), float) * 0.1
        for k, count in [("pose", 33), ("face", 468), ("left_hand", 21), ("right_hand", 21)]
    }
    a["pose"][:, 11, 0] = -0.5
    a["pose"][:, 12, 0] = 0.5
    return {
        "name": name,
        "fps": 30,
        "frames": n,
        ("coord_system"): (
            "clip shoulder center; shoulder-width unit;+X right,+Y up,+Z toward cam"
            "era; approximate model relative depth"
        ),
        **{k: v.tolist() for k, v in a.items()},
        "point_valid": {k: np.ones(v.shape[:2], bool).tolist() for k, v in a.items()},
        "point_source": {k: np.ones(v.shape[:2], int).tolist() for k, v in a.items()},
        "point_source_codes": {
            "0": "unknown",
            "1": "model coordinate",
            "2": "bounded short-gap interpolation",
            "3": "synthetic rest display fallback",
        },
        "synthetic_rest_frames": {k: [False] * n for k in ["left_hand", "right_hand"]},
        "left_hand_rest_weight": [0] * n,
        "right_hand_rest_weight": [0] * n,
    }


def register(p, word="안전"):
    row = {
        "word": word,
        "id": "test",
        "source_sha256": "test",
        "stage": "review_pending",
        "source": "sample.mp4",
        "raw": "sample_raw.json",
        "normalized_video": "sample.mp4",
        "avatar": "processed/" + word + "_avatar.json",
        "audit_file": "audit.json",
        "approval": None,
        "history": [],
    }
    p.db["items"][word] = row
    write(p.pathof(row, "avatar"), avatar(word))
    p.audit(row)
    p.save()
    return row


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_copy_waits_for_stability(self):
        f = self.root / "안전.mp4"
        f.write_bytes(b"one")
        s = StableFiles(2)
        self.assertEqual(s.ready([f], 0), [])
        self.assertEqual(s.ready([f], 1), [])
        f.write_bytes(b"two longer")
        self.assertEqual(s.ready([f], 2), [])
        self.assertEqual(s.ready([f], 3), [])
        self.assertEqual(s.ready([f], 4)[0][0], f)

    def test_pipeline_lock(self):
        with exclusive_lock(self.root / "lock"):
            with self.assertRaises(BusyError):
                with exclusive_lock(self.root / "lock"):
                    pass

    def test_no_export_before_human_review(self):
        p = Pipeline(self.root)
        register(p)
        with self.assertRaises(ValueError):
            p.export_approved()

    def test_export_loads_with_existing_loader(self):
        p = Pipeline(self.root)
        register(p)
        p.decide("안전", True, "테스트", "합성 테스트 자료 확인")
        arc = p.export_approved()
        self.assertIsNone(zipfile.ZipFile(arc).testzip())
        sys.path.insert(0, str(BASE / "tools"))
        from avatar_loader import load_clip

        d = load_clip(arc.with_suffix(""), "안전")
        self.assertEqual(len(d["hand_left"]), 12)

    def test_corrected_file_revokes_approval(self):
        p = Pipeline(self.root)
        register(p)
        p.decide("안전", True, "검수자", "확인")
        f = self.root / "corrected.json"
        write(f, avatar())
        p.replace_corrected("안전", f)
        self.assertIsNone(p.db["items"]["안전"]["approval"])
        with self.assertRaises(ValueError):
            p.export_approved()

    def test_wrong_word_or_fps_correction_rejected(self):
        p = Pipeline(self.root)
        register(p)
        f = self.root / "correction.json"
        for d in [avatar("다른단어"), {**avatar(), "fps": 60}]:
            write(f, d)
            with self.assertRaises(ValueError):
                p.replace_corrected("안전", f)

    def test_export_checks_approval_hash(self):
        p = Pipeline(self.root)
        r = register(p)
        p.decide("안전", True, "테스트", "확인")
        f = p.pathof(r, "avatar")
        f.write_text(f.read_text() + " ")
        with self.assertRaises(ValueError):
            p.export_approved()

    def test_recursive_outputs_rejected(self):
        c = config(self.root)
        d = load(c)
        d["workspace_dir"] = "incoming/work"
        write(c, d)
        with self.assertRaises(ValueError):
            Runtime(c)

    def test_duplicate_word_filenames_blocked(self):
        rt = Runtime(config(self.root))
        (rt.input / "안전.mp4").write_bytes(b"a")
        (rt.input / "안전.mov").write_bytes(b"b")
        rt.tick(clock=0)
        rt.tick(clock=2)
        self.assertIn("같은 단어", rt.scan_error)
        self.assertEqual(rt.snapshot()["items"], [])

    def test_watcher_does_not_repeat_unchanged_input(self):
        rt = Runtime(config(self.root))
        f = rt.input / "안전.mp4"
        f.write_bytes(b"synthetic")
        calls = []

        class Fake:
            db = {"items": {"안전": {"stage": "review_pending"}}}

            def process(self, files):
                calls.append(files)

        with patch.object(rt, "pipeline", return_value=Fake()):
            rt.tick(clock=0)
            rt.tick(clock=2)
            rt.tick(clock=5)
        self.assertEqual(len(calls), 1)

    def test_changed_config_refuses_resume(self):
        c = config(self.root)
        Runtime(c)
        d = load(c)
        d["processing"]["min_cutoff"] = 1.0
        write(c, d)
        with self.assertRaises(ValueError):
            Runtime(c)

    def test_http_status_range_traversal_and_csrf(self):
        rt = Runtime(config(self.root))
        f = rt.root / "sample.mp4"
        f.write_bytes(b"0123456789")
        server = ReviewServer(("127.0.0.1", 0), rt, BASE / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/status") as r:
                self.assertEqual(json.load(r)["items"], [])
            req = urllib.request.Request(
                base + "/file?path=sample.mp4", headers={"Range": "bytes=2-5"}
            )
            with urllib.request.urlopen(req) as r:
                self.assertEqual((r.status, r.read()), (206, b"2345"))
            for url in ["/file?path=../../config.json", "/file?path=.pipeline.lock"]:
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + url)
            req = urllib.request.Request(
                base + "/api/export", data=b"{}", headers={"Content-Type": "application/json"}
            )
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 403)
            bad = urllib.request.Request(
                base + "/api/export",
                data=b"{}",
                headers={"Content-Length": "invalid", "X-Review-Token": server.token},
            )
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(bad)
            self.assertEqual(e.exception.code, 400)
            req = urllib.request.Request(
                base + "/api/export",
                data=b"{}",
                headers={"Content-Type": "application/json", "X-Review-Token": server.token},
            )
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)  # valid token, but no approved data
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
