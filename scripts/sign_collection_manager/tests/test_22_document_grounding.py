import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksl_manager.db import connect
from ksl_manager.documents import candidates, original_excerpt


class DocumentGroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def extract(self, text, terms, notices=None):
        with patch("ksl_manager.documents.complete", return_value={"terms": terms}):
            return candidates(self.con, text, "합성 문서 p.1", True, diagnostics=notices)

    def test_pdf_whitespace_restores_exact_original_excerpt(self):
        text = "서문\n개인보호구를 착용하여,\n  재해를 예방합니다.\n끝"
        quote = "개인보호구를 착용하여, 재해를 예방합니다."
        result = self.extract(
            text, [{"word": "개인보호구", "sense": "보호 장비", "sentence": quote}]
        )
        self.assertEqual(result[0]["sentence"], "개인보호구를 착용하여,\n  재해를 예방합니다.")
        self.assertIn(result[0]["sentence"], text)

    def test_word_split_by_pdf_whitespace_is_accepted(self):
        result = self.extract(
            "개인 보호구를 착용합니다.",
            [{"word": "개인보호구", "sense": "보호 장비", "sentence": "개인보호구를 착용합니다."}],
        )
        self.assertEqual(result[0]["word"], "개인보호구")
        self.assertEqual(result[0]["sentence"], "개인 보호구를 착용합니다.")

    def test_invalid_candidate_does_not_discard_valid_candidates(self):
        notices = []
        result = self.extract(
            "보안경을 착용합니다.",
            [
                {"word": "보안경", "sense": "눈 보호구", "sentence": "보안경을 착용합니다."},
                {"word": "안전모", "sense": "머리 보호구", "sentence": "안전모를 착용합니다."},
                {"word": "미완성"},
            ],
            notices,
        )
        self.assertEqual([r["word"] for r in result], ["보안경"])
        self.assertIn("2개 제외", notices[0])
        self.assertEqual(self.con.execute("SELECT count(*) FROM terms").fetchone()[0], 0)

    def test_all_invalid_page_is_reported_without_aborting_batch(self):
        notices = []
        self.assertEqual(
            self.extract(
                "보안경을 착용합니다.",
                [{"word": "안전모", "sense": "머리 보호구", "sentence": "안전모를 착용합니다."}],
                notices,
            ),
            [],
        )
        self.assertEqual(len(notices), 1)

    def test_meaning_changes_and_reordered_sentences_not_accepted(self):
        text = "보안경을 착용합니다."
        self.assertIsNone(original_excerpt(text, "보안경을 착용하지 않습니다."))
        self.assertIsNone(original_excerpt(text, "착용합니다. 보안경을"))
