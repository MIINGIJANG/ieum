import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from ksl_manager.coordinate_export import CoordinateUnavailable
from ksl_manager.db import connect
from ksl_manager.metadata import import_metadata

APP = Path(__file__).resolve().parents[1] / "app.py"


class DownloadUITests(unittest.TestCase):
    def test_selected_row_has_3d_download_and_missing_data_does_not(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {"KSL_DB": str(Path(tmp) / "db"), "KSL_DATA_DIR": tmp, "OPENAI_API_KEY": ""},
            ),
        ):
            source = Path(tmp) / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "metadata": {"id": "FIXTURE"},
                        "sign_script": [{"gloss_id": "가상시험", "start": 1, "end": 2}],
                    }
                ),
                encoding="utf-8",
            )
            con = connect(Path(tmp) / "db")
            import_metadata(con, source, "합성 UI 검사")
            con.close()
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            app.sidebar.radio[0].set_value("메타데이터").run()
            original = st.dataframe

            def select_first(*args, **kwargs):
                result = original(*args, **kwargs)
                if kwargs.get("selection_mode") == "single-row":
                    return SimpleNamespace(selection=SimpleNamespace(rows=[0]))
                return result

            result = {
                "metadata": {"frame_count": 1},
                "landmarks": {"pose_keypoints_3d": [[1, 2, 3]]},
            }
            with (
                patch("streamlit.dataframe", side_effect=select_first),
                patch("ksl_manager.coordinate_export.locate_coordinates", return_value=result),
            ):
                next(x for x in app.text_input if x.label == "문장 · 글로스 검색").set_value(
                    "가상시험"
                ).run()
            self.assertFalse(app.exception)
            buttons = app.get("download_button")
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].proto.label, "선택한 단어 3D JSON 저장")
            result_2d = {
                "metadata": {"frame_count": 1, "coordinate_type": "original_2d"},
                "landmarks": {"pose_keypoints_2d": [[1, 2, 0.9]]},
            }
            with (
                patch("streamlit.dataframe", side_effect=select_first),
                patch("ksl_manager.coordinate_export.locate_coordinates", return_value=result_2d),
            ):
                next(x for x in app.button if x.label == "좌표 다시 찾기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.get("download_button")[0].proto.label, "선택한 단어 2D JSON 저장")
            with (
                patch("streamlit.dataframe", side_effect=select_first),
                patch(
                    "ksl_manager.coordinate_export.locate_coordinates",
                    side_effect=CoordinateUnavailable("좌표 없음"),
                ),
            ):
                next(x for x in app.button if x.label == "좌표 다시 찾기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("download_button")), 0)


if __name__ == "__main__":
    unittest.main()
