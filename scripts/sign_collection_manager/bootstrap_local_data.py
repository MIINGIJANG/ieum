"""보유 문장 색인만 읽어 새 관리 DB에 등록한다. 원본 파일은 변경하지 않는다."""

import argparse
import json
import time
from pathlib import Path

from ksl_manager.db import connect
from ksl_manager.documents import SEED_WORDS
from ksl_manager.metadata import import_metadata, search_metadata
from ksl_manager.metrics import finish_run, start_run
from ksl_manager.service import register


def main():
    p = argparse.ArgumentParser()
    p.add_argument("index", type=Path)
    p.add_argument("--db", type=Path, default=Path("data/signs.sqlite3"))
    args = p.parse_args()
    con = connect(args.db)
    begin = time.perf_counter()
    count = import_metadata(con, args.index, "AI Hub 114 · 보유 Training 선별 색인", "Training")
    elapsed_import = time.perf_counter() - begin
    ids = []
    for word in SEED_WORDS:
        tid, _ = register(
            con, word, "전하를 띠는 현상" if word == "대전" else "", domain="산업안전"
        )
        ids.append(tid)
    rid = start_run(con, "자동", ids, "초기 20단어", "로컬 메타데이터만; 외부 링크 제외")
    started = time.perf_counter()
    matches = []
    for word, tid in zip(SEED_WORDS, ids):
        result = search_metadata(con, word, limit=200)
        labels = sorted({r["gloss"] for r in result if r["gloss_label_match"]})
        matches.append(
            {
                "word": word,
                "gloss_labels": labels,
                "metadata_rows_shown": len(result),
                "clip_candidate_rows_shown": sum(r["clip_candidate"] for r in result),
                "external_dictionary": "미확인",
                "local_video_acquisition": "미확인",
                "note": "최대 200행 표시. 미일치는 전체 자료의 수어 부재를 뜻하지 않음.",
            }
        )
        if result:
            with con:
                con.execute(
                    "UPDATE terms SET collection_status='검색 결과 발견' WHERE id=?", (tid,)
                )
                con.execute(
                    """INSERT INTO searches(term_id,source,query,page_url,outcome,conditions,run_id)
                    VALUES (?,'보유 메타데이터',?,'','발견','의미·영상 확보 미확인',?)""",
                    (tid, word, rid),
                )
    elapsed = time.perf_counter() - started
    finish_run(con, rid, elapsed, None, 1)
    report = {
        "kind": "실제 보유 자료 검색",
        "source_index": str(args.index.resolve()),
        "records_imported": count,
        "index_seconds": elapsed_import,
        "search_seconds": elapsed,
        "term_count": 20,
        "mean_search_seconds": elapsed / 20,
        "api_requests": 0,
        "active_seconds": None,
        "results": matches,
    }
    con.close()
    out = args.db.parent / "initial_search_results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ["results", "source_index"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
