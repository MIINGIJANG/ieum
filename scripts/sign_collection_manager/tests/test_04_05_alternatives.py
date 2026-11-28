import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksl_manager.alternatives import propose, search_components
from ksl_manager.db import connect
from ksl_manager.service import register, review_alternative


class AlternativeTests(unittest.TestCase):
    def test_no_key_and_expert_gate_and_budget(self):
        with tempfile.TemporaryDirectory() as d:
            con = connect(Path(d) / "db")
            tid, _ = register(con, "대전", "전하를 띰", "신체에 전하가 축적된다.", "합성 시험")
            with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
                with self.assertRaises(ValueError):
                    propose(con, tid)

            def mock(c, p, data):
                return {
                    "candidates": [
                        {
                            "expression": "시험 후보",
                            "reason": "시험",
                            "difference": "검토",
                            "risk": "미확인",
                            "evidence": "가상 테스트",
                            "components": [
                                {"word": "전기", "sense": "물리현상"},
                                {"word": "몸", "sense": "신체"},
                            ],
                        }
                    ]
                }

            aid = propose(con, tid, call=mock)[0]
            self.assertEqual(len(search_components(con, aid, max_searches=1)), 1)
            with self.assertRaises(ValueError):
                review_alternative(con, aid, "전문가 승인", "이름", "일반 검토자", "검토")
            review_alternative(
                con, aid, "전문가 승인", "시험 검수자", "한국수어 전문가", "합성 테스트"
            )
            self.assertEqual(
                con.execute("SELECT review_status FROM alternatives").fetchone()[0], "전문가 승인"
            )
            con.close()
