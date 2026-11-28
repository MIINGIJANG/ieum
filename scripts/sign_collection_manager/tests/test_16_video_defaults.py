import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ksl_manager.db import connect
from ksl_manager.service import attach_video, get_video_defaults, register, save_video_defaults

APP = Path(__file__).resolve().parents[1] / "app.py"


class VideoDefaultsTests(unittest.TestCase):
    def test_blank_notes_are_unconfirmed_and_defaults_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            con = connect(root / "db")
            tid, _ = register(con, "시험")
            video = root / "fixture.mp4"
            video.write_bytes(b"synthetic video fixture")
            lid, _ = attach_video(con, tid, video, root / "storage")
            record = con.execute(
                "SELECT source,license_note,match_review FROM term_assets WHERE id=?", (lid,)
            ).fetchone()
            self.assertEqual(tuple(record), ("미확인", "미확인", "미확인"))
            save_video_defaults(con, "합성 시험 출처", "")
            con.close()
            con = connect(root / "db")
            self.assertEqual(
                get_video_defaults(con), {"source": "합성 시험 출처", "license_note": ""}
            )
            con.close()

    def test_common_source_applies_without_per_video_notes(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {"KSL_DB": str(Path(tmp) / "db"), "KSL_DATA_DIR": tmp, "OPENAI_API_KEY": ""},
            ),
        ):
            con = connect(Path(tmp) / "db")
            register(con, "시험")
            con.close()
            video = Path(tmp) / "fixture.mp4"
            video.write_bytes(b"synthetic video fixture")
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            app.sidebar.radio[0].set_value("데이터 관리").run()
            next(x for x in app.text_input if x.label == "공통 영상 출처").set_value(
                "합성 시험 출처"
            )
            next(x for x in app.button if x.label == "공통 설정 저장").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(
                next(x for x in app.text_input if x.label == "영상 출처").value, "합성 시험 출처"
            )
            next(x for x in app.text_input if x.label == "로컬 원본 영상 경로").set_value(
                str(video)
            )
            next(x for x in app.button if x.label == "영상 보관 · 해시 중복 확인").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            con = connect(Path(tmp) / "db")
            record = con.execute("SELECT source,license_note FROM term_assets").fetchone()
            self.assertEqual(tuple(record), ("합성 시험 출처", "미확인"))
            con.close()
