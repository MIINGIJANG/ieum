import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksl_manager.db import connect
from ksl_manager.llm import complete


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.ok = status_code == 200
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class LLMRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = connect(Path(self.tmp.name) / "test.db")
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-secret-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.con.close()
        self.tmp.cleanup()

    def test_json_mode_instruction_in_input_for_both_features(self):
        payload = {"word": "시험", "sentence": "기능을 시험한다."}

        def request(url, **kwargs):
            body = kwargs["json"]
            instruction, serialized = body["input"].split("\n", 1)
            self.assertIn("json", instruction.lower())
            self.assertEqual(json.loads(serialized), payload)
            expected = "json_schema" if purpose == "문서 핵심어 추출" else "json_object"
            self.assertEqual(body["text"]["format"]["type"], expected)
            if purpose == "문서 핵심어 추출":
                self.assertTrue(body["text"]["format"]["strict"])
            self.assertFalse(body["store"])
            return Response(
                200,
                {"output": [{"content": [{"type": "output_text", "text": '{"candidates":[]}'}]}]},
            )

        for purpose in ["대체 표현 추천", "문서 핵심어 추출"]:
            with self.subTest(purpose=purpose):
                self.assertEqual(
                    complete(self.con, purpose, payload, transport=request),
                    {"candidates": []},
                )

    def test_api_error_reason_visible_and_failed_usage_recorded(self):
        response = Response(400, {"error": {"message": "Input must contain JSON."}})
        with self.assertRaisesRegex(ValueError, "HTTP 400.*Input must contain JSON"):
            complete(self.con, "대체 표현 추천", {}, transport=lambda *a, **k: response)
        self.assertEqual(self.con.execute("SELECT status FROM api_usage").fetchone()[0], "오류")

    def test_error_details_redact_api_keys(self):
        response = Response(
            401,
            {
                "error": {
                    "message": "Invalid synthetic-secret-key and sk-another_sensitive_key12345"
                }
            },
        )
        with self.assertRaises(ValueError) as caught:
            complete(self.con, "대체 표현 추천", {}, transport=lambda *a, **k: response)
        message = str(caught.exception)
        self.assertNotIn("synthetic-secret-key", message)
        self.assertNotIn("sk-another", message)
        self.assertIn("[REDACTED]", message)

    def test_non_json_error_has_status_and_fallback(self):
        response = Response(502, ValueError("not json"))
        with self.assertRaisesRegex(ValueError, "HTTP 502.*API 오류 상세"):
            complete(self.con, "대체 표현 추천", {}, transport=lambda *a, **k: response)
