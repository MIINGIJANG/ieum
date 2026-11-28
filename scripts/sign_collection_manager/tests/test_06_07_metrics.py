import tempfile
import unittest
from pathlib import Path

from ksl_manager.db import connect
from ksl_manager.documents import candidates, input_rows
from ksl_manager.metrics import compare_runs, export_bytes, finish_run, start_run, summary
from ksl_manager.service import register


class MetricsTests(unittest.TestCase):
    def test_missing_is_not_zero_and_same_cohort(self):
        with tempfile.TemporaryDirectory() as d:
            con = connect(Path(d) / "db")
            tid, _ = register(con, "보안경", "눈 보호구")
            a = start_run(con, "수동", [tid], "동일 시험", "로컬만")
            b = start_run(con, "자동", [tid], "동일 시험", "로컬만")
            finish_run(con, a, 12, 10, 1)
            finish_run(con, b, 2, None, 1)
            self.assertEqual(len(compare_runs(con, a, b)), 2)
            self.assertIsNone(summary(con)["API 비용(USD)"])
            self.assertIn("'=", export_bytes([{"word": "=1+2"}]).decode("utf-8-sig"))
            self.assertEqual(candidates(con, "보안경을 쓰세요.", "시험")[0]["word"], "보안경")
            self.assertEqual(input_rows("단어.txt", "보안경\n접지".encode())[1]["word"], "접지")
            c = start_run(con, "자동", [tid], "다른 시험", "웹 포함")
            with self.assertRaises(ValueError):
                compare_runs(con, a, c)
            con.close()
