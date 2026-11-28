import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ksl_manager.alternatives import propose
from ksl_manager.db import connect
from ksl_manager.documents import candidates, input_rows
from ksl_manager.llm import complete
from ksl_manager.metadata import import_metadata, search_metadata
from ksl_manager.service import attach_video, confirmed_assets, delete_term, inventory, register


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.con = connect(self.p / "db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_word_parser_real_structure_synthetic_values(self):
        path = self.p / "word.json"
        path.write_text(
            json.dumps(
                {
                    "metaData": {"name": "TEST.mp4", "url": "https://example.org/fixture"},
                    "data": [{"start": 1, "end": 2, "attributes": [{"name": "시험"}]}],
                }
            ),
            encoding="utf-8",
        )
        import_metadata(self.con, path, "합성 AI Hub 004 구조")
        result = search_metadata(self.con, "시험")
        self.assertTrue(result[0]["clip_candidate"])
        self.assertEqual(result[0]["video_url"], "https://example.org/fixture")

    def test_bad_jsonl_rolls_back_file(self):
        p = self.p / "bad.jsonl"
        p.write_text('{"id":"a","sentence":"a"}\nBROKEN', encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            import_metadata(self.con, p)
        self.assertEqual(self.con.execute("SELECT count(*) FROM metadata_records").fetchone()[0], 0)

    def test_all_tabular_inputs(self):
        frame = pd.DataFrame(
            [{"id": "a", "korean_text": "확인하세요", "gloss": "확인1", "start": 1, "end": 2}]
        )
        for suffix in ["csv", "xlsx"]:
            p = self.p / ("meta." + suffix)
            if suffix == "csv":
                frame.to_csv(p, index=False)
            else:
                frame.to_excel(p, index=False)
            self.assertEqual(import_metadata(self.con, p), 1)
        buf = io.BytesIO()
        pd.DataFrame([{"단어": "보안경", "의미": "눈 보호구"}]).to_excel(buf, index=False)
        self.assertEqual(input_rows("terms.xlsx", buf.getvalue())[0]["word"], "보안경")

    def test_reuse_across_domains_missing_and_delete(self):
        tid, _ = register(self.con, "목", "신체", domain="산업안전")
        same, _ = register(self.con, "목", "신체", domain="공공기관·의료")
        self.assertEqual(tid, same)
        self.assertEqual(len(inventory(self.con)[0]["domains"].split(",")), 2)
        video = self.p / "fixture.mp4"
        video.write_bytes(b"synthetic")
        attach_video(self.con, tid, video, self.p / "storage", "합성 시험", "테스트만", match=True)
        assets = confirmed_assets(self.con, tid)
        self.assertEqual(len(assets), 1)
        Path(assets[0]["local_path"]).unlink()
        self.assertEqual(confirmed_assets(self.con, tid), [])
        delete_term(self.con, tid)
        self.assertEqual(self.con.execute("SELECT count(*) FROM assets").fetchone()[0], 1)

    def test_api_offline_response_and_usage(self):
        class Response:
            ok = True

            def json(self):
                return {
                    "output": [{"content": [{"type": "output_text", "text": '{"candidates":[]}'}]}],
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                }

        def request(url, **kwargs):
            self.assertTrue(url.endswith("/v1/responses"))
            self.assertFalse(kwargs["json"]["store"])
            return Response()

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "synthetic-key",
                "LLM_INPUT_USD_PER_MILLION": "1",
                "LLM_OUTPUT_USD_PER_MILLION": "2",
            },
        ):
            self.assertEqual(complete(self.con, "test", {}, transport=request), {"candidates": []})
        row = self.con.execute("SELECT * FROM api_usage").fetchone()
        self.assertAlmostEqual(row["estimated_usd"], 0.00012)

    def test_invalid_candidates_are_not_partially_saved(self):
        tid, _ = register(self.con, "시험어", "시험 의미", "시험 문맥")
        with self.assertRaises(ValueError):
            propose(self.con, tid, call=lambda *args: {"candidates": [{}]})
        self.assertEqual(self.con.execute("SELECT count(*) FROM alternatives").fetchone()[0], 0)

    def test_document_hallucination_not_saved(self):
        with patch(
            "ksl_manager.documents.complete",
            return_value={"terms": [{"word": "없는말", "sense": "시험", "sentence": "원문 아님"}]},
        ):
            with self.assertRaises(ValueError):
                candidates(self.con, "보안경을 쓰세요.", "시험", True)
