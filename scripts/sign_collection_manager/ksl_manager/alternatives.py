"""대체 표현은 검수 전 후보로만 저장한다."""

from pathlib import Path

from .db import rows
from .llm import complete
from .service import confirmed_assets, norm, register
from .sources import search_term


def propose(con, tid, call=None):
    term = dict(con.execute("SELECT * FROM terms WHERE id=?", (tid,)).fetchone())
    contexts = rows(con, "SELECT sentence,document_source FROM contexts WHERE term_id=?", (tid,))
    if not contexts or not any(c["sentence"].strip() for c in contexts):
        raise ValueError("먼저 원본 문장과 단어 의미를 입력하세요.")
    if term["sense"].startswith("미분류"):
        raise ValueError("동음이의어 혼동을 막기 위해 단어 의미를 먼저 명시하세요.")
    available = rows(
        con,
        """SELECT t.word,t.sense,ta.gloss,ta.match_review,ta.id AS asset_link,a.local_path
       FROM terms t JOIN term_assets ta ON ta.term_id=t.id JOIN assets a ON a.id=ta.asset_id
       WHERE ta.match_review='의미 일치 확인' ORDER BY t.id LIMIT 500""",
    )
    available = [
        {k: v for k, v in a.items() if k != "local_path"}
        for a in available
        if Path(a["local_path"]).is_file()
    ]
    payload = {
        "task": "alternative_candidates",
        "original": term,
        "contexts": contexts,
        "available_assets": available,
        "limit": 3,
    }
    reply = (call or complete)(con, "대체 표현 추천", payload)
    candidates = reply.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 3:
        raise ValueError("LLM 출력 형식 오류: 최대 3개 후보가 필요합니다.")
    # 전체 검증 후 저장: 일부 후보만 남는 오류를 막는다.
    for c in candidates:
        if not isinstance(c, dict) or any(
            not isinstance(c.get(k), str) or not c[k].strip()
            for k in ["expression", "reason", "difference", "risk", "evidence"]
        ):
            raise ValueError("후보의 이유·의미 차이·위험·근거가 부족합니다.")
        parts = c.get("components")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 12:
            raise ValueError("후보당 구성 단어는 1~12개여야 합니다.")
        if any(
            not isinstance(p, dict) or not norm(p.get("word")) or not norm(p.get("sense"))
            for p in parts
        ):
            raise ValueError("구성 단어와 의미를 확인하세요.")
    ids = []
    with con:
        for c in candidates:
            aid = con.execute(
                """INSERT INTO alternatives(term_id,expression,reason,difference,risk,evidence,generator)
               VALUES (?,?,?,?,?,?,?)""",
                (
                    tid,
                    c["expression"],
                    c["reason"],
                    c["difference"],
                    c["risk"],
                    c["evidence"],
                    "AI 추천 후보 — 검수 필요",
                ),
            ).lastrowid
            for i, p in enumerate(c["components"]):
                found = con.execute(
                    "SELECT id FROM terms WHERE word=? AND sense=?",
                    (norm(p["word"]), norm(p["sense"])),
                ).fetchone()
                con.execute(
                    "INSERT INTO components(alternative_id,word,sense,term_id,position) VALUES (?,?,?,?,?)",
                    (aid, norm(p["word"]), norm(p["sense"]), found[0] if found else None, i),
                )
            ids.append(aid)
        if ids and term["collection_status"] != "영상 확보 완료":
            con.execute(
                "UPDATE terms SET collection_status='대체 표현 후보 발견' WHERE id=?", (tid,)
            )
    return ids


def rank_candidates(con, tid):
    result = rows(con, "SELECT * FROM alternatives WHERE term_id=?", (tid,))
    for r in result:
        parts = rows(
            con,
            """SELECT c.*,EXISTS(SELECT 1 FROM term_assets ta JOIN assets a ON a.id=ta.asset_id
            WHERE ta.term_id=c.term_id AND ta.match_review='의미 일치 확인') AS acquired
            FROM components c WHERE c.alternative_id=? ORDER BY c.position""",
            (r["id"],),
        )
        for part in parts:
            part["acquired"] = bool(part["term_id"] and confirmed_assets(con, part["term_id"]))
        r["components"] = parts
        r["new_words"] = sum(not p["acquired"] for p in parts)
        # 의미 안전성은 기계 점수로 확정하지 않는다. 승인 여부를 우선한다.
    return sorted(
        result,
        key=lambda r: (
            r["review_status"] != "전문가 승인",
            r["review_status"] == "사용 불가",
            r["new_words"],
            len(r["components"]),
        ),
    )


def search_components(con, aid, max_depth=1, max_searches=12):
    if not 1 <= max_depth <= 3 or not 1 <= max_searches <= 50:
        raise ValueError("탐색 깊이는 1~3, 검색 횟수는 1~50입니다.")
    root = con.execute("SELECT term_id FROM alternatives WHERE id=?", (aid,)).fetchone()
    queue = [(aid, 1)]
    visited = set()
    searched = []
    while queue and len(searched) < max_searches:
        current, depth = queue.pop(0)
        for p in rows(
            con, "SELECT * FROM components WHERE alternative_id=? ORDER BY position", (current,)
        ):
            key = (p["word"], p["sense"])
            if key in visited or len(searched) >= max_searches:
                continue
            visited.add(key)
            tid = p["term_id"]
            if not tid:
                context = "대체 표현 구성 단어: " + p["word"]
                tid, _ = register(con, p["word"], p["sense"], context, f"대체 후보 {aid}")
                with con:
                    con.execute("UPDATE components SET term_id=? WHERE id=?", (tid, p["id"]))
            has_asset = confirmed_assets(con, tid)
            if not has_asset:
                searched.append({"term_id": tid, **search_term(con, tid)})
            if depth < max_depth and tid != root[0]:
                for child in rows(con, "SELECT id FROM alternatives WHERE term_id=?", (tid,)):
                    queue.append((child["id"], depth + 1))
    return searched
