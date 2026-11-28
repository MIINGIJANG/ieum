"""분모, 측정 범위와 미측정 항목을 함께 반환한다."""

import io
import json
import math
from pathlib import Path

import pandas as pd

from .db import rows


def start_run(con, mode, term_ids, cohort, conditions):
    if not term_ids or not cohort.strip() or not conditions.strip():
        raise ValueError("단어 집합·실험명·검색 조건이 필요합니다.")
    ids = sorted(set(int(i) for i in term_ids))
    with con:
        return con.execute(
            "INSERT INTO runs(mode,cohort,conditions,term_ids) VALUES (?,?,?,?)",
            (mode, cohort, conditions, json.dumps(ids)),
        ).lastrowid


def finish_run(con, rid, elapsed, active=None, source_count=0):
    if (
        elapsed < 0
        or not math.isfinite(elapsed)
        or active is not None
        and (active < 0 or active > elapsed or not math.isfinite(active))
    ):
        raise ValueError("시간은 유한한 비음수이며 개입 시간은 전체 시간 이하여야 합니다.")
    with con:
        con.execute(
            "UPDATE runs SET elapsed_seconds=?,active_seconds=?,source_count=? WHERE id=?",
            (elapsed, active, source_count, rid),
        )


def compare_runs(con, a, b):
    x = con.execute("SELECT * FROM runs WHERE id=?", (a,)).fetchone()
    y = con.execute("SELECT * FROM runs WHERE id=?", (b,)).fetchone()
    if (
        not x
        or not y
        or x["term_ids"] != y["term_ids"]
        or x["conditions"] != y["conditions"]
        or x["mode"] == y["mode"]
    ):
        raise ValueError("같은 단어 집합·같은 조건의 수동/자동 실행만 비교할 수 있습니다.")
    return [dict(x), dict(y)]


def summary(con):
    total = con.execute("SELECT count(*) FROM terms").fetchone()[0]
    assets = rows(
        con,
        """SELECT ta.term_id,ta.preprocessing_status,ta.match_review,a.local_path
                    FROM term_assets ta JOIN assets a ON a.id=ta.asset_id""",
    )
    acquired = {
        a["term_id"]
        for a in assets
        if a["match_review"] == "의미 일치 확인" and Path(a["local_path"]).is_file()
    }
    preprocessed = {a["term_id"] for a in assets if a["preprocessing_status"] != "미처리"}
    found = {
        r[0] for r in con.execute("SELECT DISTINCT term_id FROM searches WHERE outcome='발견'")
    }
    alt = {r[0] for r in con.execute("SELECT DISTINCT term_id FROM alternatives")}
    pending = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT term_id FROM alternatives WHERE review_status IN ('AI 추천','검수 대기','수정 필요')"
        )
    }
    approved = con.execute(
        "SELECT count(*) FROM alternatives WHERE review_status='전문가 승인'"
    ).fetchone()[0]
    reviewed = con.execute(
        "SELECT count(*) FROM alternatives WHERE review_status IN ('전문가 승인','수정 필요','사용 불가')"
    ).fetchone()[0]

    def rate(a, b):
        return round(100 * a / b, 2) if b else None

    api = rows(con, "SELECT * FROM api_usage")
    times = rows(con, "SELECT * FROM runs WHERE elapsed_seconds IS NOT NULL")
    return {
        "전체 단어·의미": total,
        "검색 결과 발견 단어": len(found),
        "의미 일치 영상 확보 단어": len(acquired),
        "대체 후보 단어": len(alt),
        "미수집 단어": total - len(acquired),
        "검수 대기 단어": len(pending),
        "좌표 처리 기록 단어": len(preprocessed),
        "검색 결과 발견율(%)": rate(len(found), total),
        "실제 영상 확보율(%)": rate(len(acquired), total),
        "대체 후보 확보율(%)": rate(len(alt), total),
        "전문가 승인율(검토 후보 기준,%)": rate(approved, reviewed),
        "영상 중복 방지 건수": con.execute(
            "SELECT count(*) FROM events WHERE event='video_duplicate_prevented'"
        ).fetchone()[0],
        "영상 재사용 건수": con.execute(
            "SELECT count(*) FROM events WHERE event='asset_reused'"
        ).fetchone()[0],
        "API 요청 횟수": len(api),
        "API 비용(USD)": sum(a["estimated_usd"] for a in api)
        if api and all(a["estimated_usd"] is not None for a in api)
        else None,
        "측정 실행": times,
        "비고": "등록 단어 전체를 분모로 한 현황 지표. 번역 정확도 아님. 미측정값은 null.",
    }


def safe_frame(records):
    df = pd.DataFrame(records)
    # CSV/Excel에서 사용자 입력이 수식으로 실행되지 않게 한다.
    for column in df.columns:
        df[column] = df[column].map(
            lambda v: (
                "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else v
            )
        )
    return df


def export_bytes(records, kind="csv"):
    df = safe_frame(records)
    if kind == "csv":
        return df.to_csv(index=False).encode("utf-8-sig")
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()
