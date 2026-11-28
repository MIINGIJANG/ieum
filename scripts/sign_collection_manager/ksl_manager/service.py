"""문맥별 단어 등록, 원본 보관, 상태 변경 및 검수."""

import hashlib
import json
import shutil
import unicodedata
from pathlib import Path

from .db import rows

PREPROCESS = ["미처리", "좌표 추출 완료", "품질 검수 필요", "검수 완료", "팀원 전달 완료"]
COLLECTION = [
    "미검색",
    "검색 중",
    "검색 결과 발견",
    "영상 확보 완료",
    "대체 표현 후보 발견",
    "검색 실패",
]
REVIEW = ["AI 추천", "검수 대기", "전문가 승인", "수정 필요", "사용 불가"]


def norm(value):
    return unicodedata.normalize("NFC", str(value or "")).strip()


def event(con, name, detail):
    con.execute(
        "INSERT INTO events(event,detail) VALUES (?,?)",
        (name, json.dumps(detail, ensure_ascii=False)),
    )


def register(con, word, sense="", sentence="", source="", domain="공통"):
    word, sense = norm(word), norm(sense)
    if not word:
        raise ValueError("단어를 입력하세요.")
    # 문맥이 다른 미분류 단어를 같은 의미라고 가정하지 않는다.
    sense = sense or ("미분류: " + norm(sentence) if sentence else "미분류")
    previous = con.execute(
        "SELECT id FROM terms WHERE word=? AND sense=?", (word, sense)
    ).fetchone()
    if previous:
        tid = previous["id"]
        event(con, "term_duplicate_prevented", {"term_id": tid})
    else:
        tid = con.execute(
            "INSERT INTO terms(word,sense,domain) VALUES (?,?,?)", (word, sense, norm(domain))
        ).lastrowid
    con.execute(
        "INSERT OR IGNORE INTO term_domains(term_id,domain) VALUES (?,?)", (tid, norm(domain))
    )
    if sentence or source:
        con.execute(
            "INSERT OR IGNORE INTO contexts(term_id,sentence,document_source) VALUES (?,?,?)",
            (tid, sentence, source),
        )
    con.commit()
    return tid, bool(previous)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attach_video(
    con, tid, path, storage, source="", license_note="", page_url="", gloss="", match=False
):
    p = Path(path).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v"}:
        raise ValueError("존재하는 영상 파일을 선택하세요.")
    source = norm(source) or "미확인"
    license_note = norm(license_note) or "미확인"
    digest = sha256(p)
    with con:
        asset = con.execute("SELECT * FROM assets WHERE sha256=?", (digest,)).fetchone()
        if asset:
            target = Path(asset["local_path"])
            if not target.is_file() or sha256(target) != digest:
                raise ValueError("기존 보관 파일이 없거나 변경됐습니다. 먼저 원본을 복구하세요.")
            aid = asset["id"]
            event(con, "video_duplicate_prevented", {"asset_id": aid})
        else:
            target = Path(storage) / "videos" / (digest + p.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            aid = con.execute(
                "INSERT INTO assets(sha256,local_path,bytes,original_name) VALUES (?,?,?,?)",
                (digest, str(target.resolve()), p.stat().st_size, p.name),
            ).lastrowid
        link = con.execute(
            "SELECT id FROM term_assets WHERE term_id=? AND asset_id=?", (tid, aid)
        ).fetchone()
        if link:
            event(con, "asset_reused", {"term_id": tid, "asset_id": aid})
            return link["id"], True
        lid = con.execute(
            """INSERT INTO term_assets(term_id,asset_id,source,license_note,page_url,gloss,match_review)
                           VALUES (?,?,?,?,?,?,?)""",
            (
                tid,
                aid,
                source,
                license_note,
                page_url,
                gloss,
                "의미 일치 확인" if match else "미확인",
            ),
        ).lastrowid
        if asset:
            event(con, "asset_reused", {"term_id": tid, "asset_id": aid})
        if match:
            con.execute(
                "UPDATE terms SET collection_status='영상 확보 완료',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (tid,),
            )
    return lid, bool(asset)


def update_asset(con, lid, status, coordinate_path="", match_review="미확인"):
    if status not in PREPROCESS:
        raise ValueError("지원하지 않는 전처리 상태입니다.")
    if coordinate_path and not Path(coordinate_path).expanduser().is_file():
        raise ValueError("좌표 파일을 찾지 못했습니다.")
    if status != "미처리" and not coordinate_path:
        raise ValueError("좌표 파일 경로가 필요합니다.")
    with con:
        con.execute(
            """UPDATE term_assets SET preprocessing_status=?,coordinate_path=?,match_review=?,
                       delivered_at=CASE WHEN ?='팀원 전달 완료' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?""",
            (status, coordinate_path or None, match_review, status, lid),
        )
        event(con, "asset_status_changed", {"id": lid, "status": status})
        tid = con.execute("SELECT term_id FROM term_assets WHERE id=?", (lid,)).fetchone()[0]
        available = confirmed_assets(con, tid)
        if available:
            con.execute("UPDATE terms SET collection_status='영상 확보 완료' WHERE id=?", (tid,))
        else:
            con.execute(
                "UPDATE terms SET collection_status='검색 중' WHERE id=? AND collection_status='영상 확보 완료'",
                (tid,),
            )


def confirmed_assets(con, tid):
    """의미 일치 확인과 실제 보관 파일 존재를 함께 검사한다."""
    return [
        r
        for r in rows(
            con,
            """SELECT a.*,ta.id AS link_id FROM assets a
        JOIN term_assets ta ON ta.asset_id=a.id WHERE ta.term_id=?
        AND ta.match_review='의미 일치 확인'""",
            (tid,),
        )
        if Path(r["local_path"]).is_file()
    ]


def set_collection(con, tid, status):
    if status not in COLLECTION:
        raise ValueError("지원하지 않는 수집 상태입니다.")
    if status == "영상 확보 완료":
        found = con.execute(
            """SELECT a.local_path FROM term_assets ta JOIN assets a ON a.id=ta.asset_id
                            WHERE ta.term_id=? AND ta.match_review='의미 일치 확인' """,
            (tid,),
        ).fetchall()
        if not any(Path(r[0]).is_file() for r in found):
            raise ValueError("의미 일치가 확인된 로컬 영상이 필요합니다.")
    with con:
        con.execute(
            "UPDATE terms SET collection_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, tid),
        )


def review_alternative(con, aid, decision, reviewer, role, note, expression=""):
    if decision not in REVIEW:
        raise ValueError("지원하지 않는 검수 상태입니다.")
    if not reviewer.strip() or not note.strip():
        raise ValueError("검수자와 검토 근거를 입력하세요.")
    if decision == "전문가 승인" and role not in ["농인 검수자", "한국수어 전문가"]:
        raise ValueError("농인 또는 한국수어 전문가의 검수가 필요합니다.")
    with con:
        con.execute("UPDATE alternatives SET review_status=? WHERE id=?", (decision, aid))
        if expression:
            # 표현 수정은 과거 구성 단어의 승인으로 오인하지 않게 재검토한다.
            con.execute(
                "UPDATE alternatives SET expression=?,review_status='수정 필요' WHERE id=?",
                (expression, aid),
            )
        con.execute(
            "INSERT INTO reviews(alternative_id,reviewer,role,decision,note) VALUES (?,?,?,?,?)",
            (aid, reviewer, role, decision, note),
        )
    return "수정 필요" if expression else decision


def inventory(con):
    return rows(
        con,
        """SELECT t.*,
        (SELECT group_concat(domain, ',') FROM term_domains WHERE term_id=t.id) AS domains,
        group_concat(DISTINCT ta.source) AS sources,
        count(DISTINCT ta.asset_id) AS linked_assets,
        (SELECT group_concat(sentence, ' | ') FROM contexts WHERE term_id=t.id) AS original_sentences,
        (SELECT group_concat(document_source, ' | ') FROM contexts WHERE term_id=t.id) AS document_sources,
        group_concat(DISTINCT ta.coordinate_path) AS coordinate_paths,
        group_concat(DISTINCT ta.delivered_at) AS delivered_at,
        group_concat(DISTINCT ta.preprocessing_status) AS preprocessing
        FROM terms t LEFT JOIN term_assets ta ON ta.term_id=t.id GROUP BY t.id ORDER BY t.id""",
    )


def delete_term(con, tid):
    with con:
        event(con, "term_deleted", {"id": tid})
        con.execute("DELETE FROM terms WHERE id=?", (tid,))
    # 원본 파일과 해시 보관은 유지한다.


def save_recommendation_context(con, tid, sense, sentence, source=""):
    """미분류 의미와 추천에 필요한 원문을 같은 화면에서 저장한다."""
    sense, sentence, source = norm(sense), str(sentence).strip(), str(source).strip()
    if not sense or sense.startswith("미분류") or not sentence:
        raise ValueError("문맥상 의미와 원본 문장을 모두 입력하세요.")
    term = con.execute("SELECT * FROM terms WHERE id=?", (tid,)).fetchone()
    if not term:
        raise ValueError("단어를 찾지 못했습니다.")
    if not term["sense"].startswith("미분류") and term["sense"] != sense:
        raise ValueError("이미 분류된 단어와 다른 뜻이면 단어 검색에서 별도 의미로 등록하세요.")
    duplicate = con.execute(
        "SELECT id FROM terms WHERE word=? AND sense=? AND id<>?", (term["word"], sense, tid)
    ).fetchone()
    if duplicate:
        raise ValueError(f"같은 단어·의미가 이미 있습니다. 목록에서 #{duplicate[0]}을 선택하세요.")
    with con:
        con.execute(
            "UPDATE terms SET sense=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (sense, tid)
        )
        con.execute(
            "INSERT OR IGNORE INTO contexts(term_id,sentence,document_source) VALUES (?,?,?)",
            (tid, sentence, source),
        )
        event(con, "recommendation_context_saved", {"term_id": tid})
    return True


def get_video_defaults(con):
    row = con.execute("SELECT value FROM app_settings WHERE key='video_defaults'").fetchone()
    return json.loads(row[0]) if row else {"source": "", "license_note": ""}


def save_video_defaults(con, source="", license_note=""):
    value = json.dumps(
        {"source": norm(source), "license_note": norm(license_note)}, ensure_ascii=False
    )
    with con:
        con.execute(
            "INSERT INTO app_settings(key,value) VALUES ('video_defaults',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )
    return True
