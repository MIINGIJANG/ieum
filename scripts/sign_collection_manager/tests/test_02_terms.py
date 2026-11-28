import tempfile
import unittest
from pathlib import Path

from ksl_manager.db import connect
from ksl_manager.service import attach_video, register, set_collection


class TermsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.con = connect(self.p / "test.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_senses_and_context(self):
        a, _ = register(self.con, "대전", "帶電", "신체에 전하가 축적된다.", "가이드")
        b, _ = register(self.con, "대전", "지역명", "대전에서 근무한다.", "가이드")
        c, duplicate = register(self.con, "대전", "帶電", "작업복에 전하가 축적된다.", "가이드")
        self.assertEqual(a, c)
        self.assertNotEqual(a, b)
        self.assertTrue(duplicate)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM contexts WHERE term_id=?", (a,)).fetchone()[0], 2
        )

    def test_hash_and_acquisition(self):
        tid, _ = register(self.con, "얼굴", "신체 부위")
        v = self.p / "v.mp4"
        v.write_bytes(b"fixture video bytes; not playable")
        attach_video(self.con, tid, v, self.p / "storage", "가상 테스트", "합성 테스트", match=True)
        attach_video(self.con, tid, v, self.p / "storage", "가상 테스트", "합성 테스트", match=True)
        self.assertEqual(self.con.execute("SELECT count(*) FROM assets").fetchone()[0], 1)
        self.assertEqual(
            self.con.execute("SELECT collection_status FROM terms").fetchone()[0], "영상 확보 완료"
        )
        other, _ = register(self.con, "목", "신체 부위")
        with self.assertRaises(ValueError):
            set_collection(self.con, other, "영상 확보 완료")
