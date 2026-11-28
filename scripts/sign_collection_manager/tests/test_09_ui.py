import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class UITests(unittest.TestCase):
    def test_pages_and_registration(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(
                os.environ, {"KSL_DATA_DIR": d, "KSL_DB": str(Path(d) / "db"), "OPENAI_API_KEY": ""}
            ),
        ):
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20
            ).run()
            self.assertFalse(app.exception)
            for page in [
                "단어 검색",
                "대체 표현",
                "데이터 관리",
                "메타데이터",
                "통계·작업",
            ]:
                app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(app.exception, f"{page}: {list(app.exception)}")
            app.sidebar.radio[0].set_value("단어 검색").run()
            button = next(x for x in app.button if x.label.startswith("초기 테스트"))
            button.click().run()
            self.assertFalse(app.exception)
            for page in [
                "대시보드",
                "단어 검색",
                "대체 표현",
                "데이터 관리",
                "메타데이터",
                "통계·작업",
            ]:
                app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(app.exception, f"{page}: {list(app.exception)}")
