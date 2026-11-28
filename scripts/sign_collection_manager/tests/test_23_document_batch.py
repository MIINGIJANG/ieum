import json
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from ksl_manager.db import connect
from ksl_manager.document_batch import cache_path, collect_document_candidates
from ksl_manager.llm import IncompleteResponseError, InvalidJSONResponseError, complete


class DocumentBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.con = connect(self.root / "test.db")
        self.cache = self.root / "cache"

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    @staticmethod
    def extract(con, text, source, use_llm, diagnostics):
        return [
            {
                "word": text.split()[0],
                "sense": "합성 의미",
                "sentence": text,
                "source": source,
                "method": "합성 시험",
            }
        ]

    def test_successful_chunks_reused_without_api_call(self):
        records = [{"sentence": "보안경 착용", "source": "합성 p.1"}]
        with patch("ksl_manager.document_batch.candidates", side_effect=self.extract) as request:
            first = collect_document_candidates(self.con, records, True, self.cache)
            second = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(second["stats"]["cache_hits"], 1)

    def test_mac_filename_normalization_reuses_completed_chunk(self):
        source = "개인보호구.pdf p.1"
        records = [{"sentence": "보안경 착용", "source": source}]
        with patch("ksl_manager.document_batch.candidates", side_effect=self.extract) as request:
            collect_document_candidates(self.con, records, True, self.cache)
            records[0]["source"] = unicodedata.normalize("NFD", source)
            cached = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(cached["stats"]["cache_hits"], 1)
        self.assertEqual(cached["candidates"][0]["source"], records[0]["source"])

    def test_partial_failure_preserves_results_and_retries_only_failed(self):
        records = [
            {"sentence": "보안경 착용", "source": "합성 p.1"},
            {"sentence": "안전모 착용", "source": "합성 p.2"},
        ]

        def extract(con, text, source, use_llm, diagnostics):
            if source == "합성 p.2":
                raise InvalidJSONResponseError("미완료 합성 응답")
            return self.extract(con, text, source, use_llm, diagnostics)

        with patch("ksl_manager.document_batch.candidates", side_effect=extract):
            first = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(first["stats"]["failed"], 1)
        self.assertEqual([r["word"] for r in first["candidates"]], ["보안경"])
        with patch("ksl_manager.document_batch.candidates", side_effect=self.extract) as request:
            second = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(second["stats"]["completed"], 2)
        self.assertEqual(len(second["candidates"]), 2)

    def test_truncated_chunk_split_has_bounded_recovery(self):
        records = [{"sentence": "가" * 600, "source": "합성 p.1"}]
        sizes = []

        def extract(con, text, source, use_llm, diagnostics):
            sizes.append(len(text))
            if len(text) > 400:
                raise IncompleteResponseError("max_output_tokens")
            return self.extract(con, text, source, use_llm, diagnostics)

        with patch("ksl_manager.document_batch.candidates", side_effect=extract):
            result = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(result["stats"]["failed"], 0)
        self.assertTrue(result["candidates"])
        self.assertLessEqual(len(sizes), 5)
        self.assertLess(max(sizes[1:]), sizes[0])

    def test_quota_error_stops_remaining_calls_but_keeps_completed(self):
        records = [{"sentence": f"시험{i} 문장", "source": f"합성 p.{i}"} for i in range(3)]
        with patch(
            "ksl_manager.document_batch.candidates",
            side_effect=[
                self.extract(None, "시험0 문장", "합성 p.0", True, []),
                ValueError("HTTP 429. credit_balance_exhausted"),
            ],
        ) as request:
            result = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["stats"]["failed"], 2)
        self.assertEqual(len(result["candidates"]), 1)

    def test_unverified_cache_quote_not_used(self):
        records = [{"sentence": "보안경 착용", "source": "합성 p.1"}]
        with patch("ksl_manager.document_batch.candidates", side_effect=self.extract):
            collect_document_candidates(self.con, records, True, self.cache)
        path = cache_path(self.cache, "보안경 착용", "합성 p.1", True)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["candidates"][0]["sentence"] = "원문에 없는 문장"
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch("ksl_manager.document_batch.candidates", side_effect=self.extract) as request:
            result = collect_document_candidates(self.con, records, True, self.cache)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["candidates"][0]["sentence"], "보안경 착용")

    def test_direct_records_and_offline_search_do_not_use_llm_cache(self):
        record = {"word": "보안경", "sense": "눈 보호구", "sentence": "", "source": "합성"}
        result = collect_document_candidates(self.con, [record], False, self.cache)
        self.assertEqual(result["candidates"], [record])
        self.assertFalse(self.cache.exists())


class LLMResponseCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = connect(Path(self.tmp.name) / "test.db")
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.con.close()
        self.tmp.cleanup()

    def call(self, body):
        class Response:
            ok = True

            def json(self):
                return body

        return complete(self.con, "문서 핵심어 추출", {}, transport=lambda *a, **k: Response())

    def test_incomplete_response_not_parsed_or_marked_successful(self):
        with self.assertRaises(IncompleteResponseError):
            self.call(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [{"content": [{"type": "output_text", "text": '{"terms":['}]}],
                    "usage": {"input_tokens": 10, "output_tokens": 2500},
                }
            )
        row = self.con.execute("SELECT status,output_tokens FROM api_usage").fetchone()
        self.assertEqual(tuple(row), ("오류", 2500))

    def test_invalid_completed_json_has_recoverable_error(self):
        with self.assertRaises(InvalidJSONResponseError):
            self.call(
                {
                    "status": "completed",
                    "output": [{"content": [{"type": "output_text", "text": '{"terms":['}]}],
                }
            )

    def test_refusal_not_passed_to_json_parser(self):
        with self.assertRaisesRegex(ValueError, "거절"):
            self.call(
                {
                    "status": "completed",
                    "output": [{"content": [{"type": "refusal", "refusal": "합성"}]}],
                }
            )
