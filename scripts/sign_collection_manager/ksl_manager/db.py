"""SQLite 연결 및 마이그레이션 초기화."""

import os
import sqlite3
from pathlib import Path


def connect(path=None):
    path = Path(path or os.getenv("KSL_DB", "data/signs.sqlite3")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    columns = {r[1] for r in con.execute("PRAGMA table_info(metadata_records)")}
    for name in ["video_url", "original_record_path"]:
        if name not in columns:
            con.execute(f"ALTER TABLE metadata_records ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    con.commit()
    return con


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]
