import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ksl_manager.db import connect

APP = Path(__file__).resolve().parents[1] / "app.py"


class CandidateSelectionTests(unittest.TestCase):
    def test_unselected_candidates_are_not_registered(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(
                os.environ, {"KSL_DB": str(Path(d) / "db"), "KSL_DATA_DIR": d, "OPENAI_API_KEY": ""}
            ),
        ):
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            app.session_state["input_candidates"] = [
                {
                    "word": "가상후보A",
                    "sense": "시험 의미 A",
                    "sentence": "원문A",
                    "source": "합성 시험",
                },
                {
                    "word": "가상후보B",
                    "sense": "시험 의미 B",
                    "sentence": "원문B",
                    "source": "합성 시험",
                },
            ]
            app.sidebar.radio[0].set_value("단어 검색").run()
            self.assertFalse(app.exception)
            button = next(b for b in app.button if b.label == "선택한 후보 등록")
            self.assertTrue(button.disabled)

            def choose_second(frame, **kwargs):
                self.assertIn("등록", kwargs["column_config"])
                frame = frame.copy()
                frame.loc[1, "등록"] = True
                frame.loc[1, "sense"] = "수정한 의미"
                return frame

            with patch("streamlit.data_editor", side_effect=choose_second):
                app.run()
                next(b for b in app.button if b.label == "선택한 후보 등록").click().run()
            self.assertFalse(app.exception)
            con = connect(Path(d) / "db")
            result = [tuple(r) for r in con.execute("SELECT word,sense FROM terms")]
            con.close()
            self.assertEqual(result, [("가상후보B", "수정한 의미")])
            self.assertTrue(any("선택한 후보 1개" in s.value for s in app.success))


if __name__ == "__main__":
    unittest.main()
