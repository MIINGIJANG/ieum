import json
import tempfile
import unittest
from pathlib import Path

from ksl_manager.db import connect
from ksl_manager.metadata import import_metadata, search_metadata
from ksl_manager.service import register
from ksl_manager.sources import search_term


class MetadataTests(unittest.TestCase):
    def test_timestamps_and_links_not_hits(self):
        with tempfile.TemporaryDirectory() as d:
            con = connect(Path(d) / "db")
            p = Path(d) / "m.json"
            p.write_text(
                json.dumps(
                    {
                        "metadata": {"id": "TEST"},
                        "korean_text": "위험을 확인",
                        "sign_script": [
                            {"gloss_id": "위험1", "start": 1, "end": 2},
                            {"gloss_id": "확인1"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(import_metadata(con, p), 1)
            self.assertEqual(import_metadata(con, p), 0)
            self.assertTrue(any(r["clip_candidate"] for r in search_metadata(con, "위험")))
            self.assertFalse(any(r["clip_candidate"] for r in search_metadata(con, "확인")))
            tid, _ = register(con, "미확인단어")
            r = search_term(con, tid)
            self.assertEqual(r["metadata"], [])
            self.assertEqual(
                con.execute("SELECT count(*) FROM searches WHERE outcome='발견'").fetchone()[0], 0
            )
            self.assertEqual(search_metadata(con, "' OR 1=1 --"), [])
            con.close()
