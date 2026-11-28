import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ksl_manager.db import connect
from ksl_manager.service import register, save_recommendation_context

APP = Path(__file__).resolve().parents[1] / "app.py"


class RecommendationContextTests(unittest.TestCase):
    def test_missing_and_duplicate_context_do_not_change_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "db")
            tid, _ = register(con, "저온")
            with self.assertRaises(ValueError):
                save_recommendation_context(con, tid, "", "")
            register(con, "저온", "온도가 낮음")
            with self.assertRaises(ValueError):
                save_recommendation_context(con, tid, "온도가 낮음", "합성 시험 문장")
            self.assertEqual(
                con.execute("SELECT sense FROM terms WHERE id=?", (tid,)).fetchone()[0], "미분류"
            )
            con.close()

    def test_context_is_saved_for_same_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "db")
            tid, _ = register(con, "저온")
            save_recommendation_context(
                con, tid, "온도가 낮은 상태", "합성 시험: 저온 환경이다.", "합성 시험"
            )
            self.assertEqual(con.execute("SELECT count(*) FROM terms").fetchone()[0], 1)
            self.assertEqual(
                con.execute("SELECT sentence FROM contexts WHERE term_id=?", (tid,)).fetchone()[0],
                "합성 시험: 저온 환경이다.",
            )
            con.close()

    def test_screen_can_save_context_then_send_it_to_llm(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    "KSL_DB": str(Path(tmp) / "db"),
                    "KSL_DATA_DIR": tmp,
                    "OPENAI_API_KEY": "synthetic-test-key",
                },
            ),
        ):
            con = connect(Path(tmp) / "db")
            register(con, "저온")
            con.close()
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            app.sidebar.radio[0].set_value("대체 표현").run()
            next(c for c in app.checkbox if c.label.startswith("단어·원문")).check().run()
            self.assertTrue(
                next(b for b in app.button if b.label == "대체 표현 후보 추천").disabled
            )
            next(
                t for t in app.text_input if t.label == "이 문장에서 단어가 뜻하는 의미"
            ).set_value("온도가 낮은 상태")
            next(t for t in app.text_area if t.label == "대체 표현에 사용할 원본 문장").set_value(
                "합성 시험: 저온 환경이다."
            )
            next(b for b in app.button if b.label == "문맥 저장").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(
                next(b for b in app.button if b.label == "대체 표현 후보 추천").disabled
            )

            def response(con, purpose, payload):
                self.assertEqual(payload["original"]["sense"], "온도가 낮은 상태")
                self.assertEqual(payload["contexts"][0]["sentence"], "합성 시험: 저온 환경이다.")
                return {"candidates": []}

            with patch("ksl_manager.alternatives.complete", side_effect=response) as api:
                next(b for b in app.button if b.label == "대체 표현 후보 추천").click().run()
                self.assertEqual(api.call_count, 1)
            self.assertFalse(app.exception)
