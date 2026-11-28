PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS terms (
 id INTEGER PRIMARY KEY, word TEXT NOT NULL, sense TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '공통',
 collection_status TEXT NOT NULL DEFAULT '미검색' CHECK(collection_status IN ('미검색','검색 중','검색 결과 발견','영상 확보 완료','대체 표현 후보 발견','검색 실패')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(word,sense)
);
CREATE TABLE IF NOT EXISTS contexts (
 id INTEGER PRIMARY KEY, term_id INTEGER NOT NULL REFERENCES terms ON DELETE CASCADE,
 sentence TEXT NOT NULL, document_source TEXT NOT NULL, UNIQUE(term_id,sentence,document_source)
);
CREATE TABLE IF NOT EXISTS term_domains (
 term_id INTEGER NOT NULL REFERENCES terms ON DELETE CASCADE,
 domain TEXT NOT NULL, PRIMARY KEY(term_id,domain)
);
CREATE TABLE IF NOT EXISTS assets (
 id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, local_path TEXT NOT NULL,
 bytes INTEGER NOT NULL, original_name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS term_assets (
 id INTEGER PRIMARY KEY, term_id INTEGER NOT NULL REFERENCES terms ON DELETE CASCADE,
 asset_id INTEGER NOT NULL REFERENCES assets, gloss TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL, page_url TEXT NOT NULL DEFAULT '', license_note TEXT NOT NULL,
 match_review TEXT NOT NULL DEFAULT '미확인' CHECK(match_review IN ('미확인','의미 일치 확인','의미 불일치')),
 preprocessing_status TEXT NOT NULL DEFAULT '미처리' CHECK(preprocessing_status IN ('미처리','좌표 추출 완료','품질 검수 필요','검수 완료','팀원 전달 완료')),
 coordinate_path TEXT, delivered_at TEXT, UNIQUE(term_id,asset_id)
);
CREATE TABLE IF NOT EXISTS searches (
 id INTEGER PRIMARY KEY, term_id INTEGER NOT NULL REFERENCES terms ON DELETE CASCADE,
 source TEXT NOT NULL, query TEXT NOT NULL, page_url TEXT NOT NULL, found_headword TEXT,
 video_url TEXT, outcome TEXT NOT NULL CHECK(outcome IN ('검색 링크','발견','미확인','검색 실패')),
 semantic_match TEXT NOT NULL DEFAULT '미확인', download_status TEXT NOT NULL DEFAULT '수동 확인 필요',
 conditions TEXT NOT NULL DEFAULT '', searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 run_id INTEGER REFERENCES runs ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS metadata_records (
 id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, source_path TEXT NOT NULL, source_hash TEXT NOT NULL,
 record_key TEXT NOT NULL, korean_text TEXT NOT NULL DEFAULT '', video_path TEXT NOT NULL DEFAULT '',
 nms_json TEXT NOT NULL DEFAULT '{}', split TEXT NOT NULL DEFAULT '미확인',
 video_url TEXT NOT NULL DEFAULT '', original_record_path TEXT NOT NULL DEFAULT '',
 UNIQUE(source_hash,record_key)
);
CREATE TABLE IF NOT EXISTS gloss_segments (
 id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES metadata_records ON DELETE CASCADE,
 gloss TEXT NOT NULL, start REAL, end REAL, source_field TEXT NOT NULL,
 reliable INTEGER NOT NULL DEFAULT 0 CHECK(reliable IN (0,1))
);
CREATE INDEX IF NOT EXISTS gloss_lookup ON gloss_segments(gloss);
CREATE TABLE IF NOT EXISTS alternatives (
 id INTEGER PRIMARY KEY, term_id INTEGER NOT NULL REFERENCES terms ON DELETE CASCADE,
 expression TEXT NOT NULL, reason TEXT NOT NULL, difference TEXT NOT NULL, risk TEXT NOT NULL,
 evidence TEXT NOT NULL, generator TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'AI 추천' CHECK(review_status IN ('AI 추천','검수 대기','전문가 승인','수정 필요','사용 불가')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS components (
 id INTEGER PRIMARY KEY, alternative_id INTEGER NOT NULL REFERENCES alternatives ON DELETE CASCADE,
 word TEXT NOT NULL, sense TEXT NOT NULL, term_id INTEGER REFERENCES terms ON DELETE SET NULL,
 position INTEGER NOT NULL, UNIQUE(alternative_id,position)
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY, alternative_id INTEGER NOT NULL REFERENCES alternatives ON DELETE CASCADE,
 reviewer TEXT NOT NULL, role TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, event TEXT NOT NULL, detail TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('수동','자동')),
 cohort TEXT NOT NULL, conditions TEXT NOT NULL, term_ids TEXT NOT NULL,
 elapsed_seconds REAL, active_seconds REAL, source_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS api_usage (
 id INTEGER PRIMARY KEY, purpose TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER,
 output_tokens INTEGER, estimated_usd REAL, status TEXT NOT NULL, duration REAL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
