import sqlite3
import tempfile
import unittest
from pathlib import Path

from ksl_manager.db import connect


class DatabaseTests(unittest.TestCase):
    def test_schema_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as d:
            con = connect(Path(d) / "test.db")
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO contexts(term_id,sentence,document_source) VALUES (999,'a','b')"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO terms(word,sense,collection_status) VALUES ('a','b','완료')"
                )
            con.close()
