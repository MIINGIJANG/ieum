"""좌표 본문을 DB에 복제하지 않는 메타데이터 파서 레지스트리."""

import json
import math
import re
from pathlib import Path

from .db import rows
from .service import norm, sha256

PARSERS = []


def parser(predicate):
    def decorate(fn):
        PARSERS.append((predicate, fn))
        return fn

    return decorate


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def segments(value, path="sign_script"):
    if isinstance(value, dict):
        label = value.get("gloss_id", value.get("gloss"))
        if label:
            start, end = number(value.get("start")), number(value.get("end"))
            # 구간 필드가 실제 존재하는 유한한 비음수 시간일 때만 후보로 사용.
            good = start is not None and end is not None and 0 <= start < end
            yield {
                "gloss": norm(label),
                "start": start,
                "end": end,
                "source_field": path,
                "reliable": int(good),
            }
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from segments(child, path + "." + key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from segments(child, f"{path}[{i}]")


@parser(lambda d: isinstance(d, dict) and ("sign_script" in d or "gloss_segments" in d))
def parse_aihub(d):
    m = d.get("metadata") or {}
    return {
        "record_key": str(m.get("id") or d.get("id") or ""),
        "korean_text": str(d.get("korean_text") or ""),
        "video_path": str(
            m.get("video_path") or m.get("video_filename") or d.get("video_path") or ""
        ),
        "nms_json": json.dumps(d.get("nms_script") or {}, ensure_ascii=False),
        "segments": list(segments(d.get("sign_script", d.get("gloss_segments")))),
    }


@parser(
    lambda d: (
        isinstance(d, dict)
        and isinstance(d.get("metaData"), dict)
        and isinstance(d.get("data"), list)
    )
)
def parse_aihub_word(d):
    """AI Hub 004 단어 형태소의 attributes[].name과 실제 구간을 읽는다."""
    meta = d["metaData"]
    spans = []
    for i, item in enumerate(d["data"]):
        for j, attr in enumerate(item.get("attributes", [])):
            if attr.get("name"):
                spans.extend(
                    segments(
                        {"gloss": attr["name"], "start": item.get("start"), "end": item.get("end")},
                        f"data[{i}].attributes[{j}]",
                    )
                )
    return {
        "record_key": str(meta.get("name") or ""),
        "korean_text": "",
        "video_path": str(meta.get("name") or ""),
        "video_url": str(meta.get("url") or ""),
        "nms_json": "{}",
        "segments": spans,
    }


@parser(lambda d: isinstance(d, dict))
def parse_generic(d):
    text = str(d.get("korean_text") or d.get("sentence") or d.get("문장") or "")
    label = d.get("gloss") or d.get("gloss_id") or d.get("글로스")
    return {
        "record_key": str(d.get("id") or d.get("word_id") or ""),
        "korean_text": text,
        "video_path": str(d.get("video_path") or d.get("video_filename") or ""),
        "nms_json": json.dumps(d.get("nms_script") or {}, ensure_ascii=False),
        "segments": list(
            segments({"gloss": label, "start": d.get("start"), "end": d.get("end")}, "row")
        )
        if label
        else [],
    }


def read_records(path):
    p = Path(path)
    if p.suffix.lower() == ".jsonl":
        with p.open(encoding="utf-8-sig") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    elif p.suffix.lower() in {".csv", ".xlsx"}:
        import pandas as pd

        df = (
            pd.read_csv(p, keep_default_na=False)
            if p.suffix.lower() == ".csv"
            else pd.read_excel(p, keep_default_na=False)
        )
        yield from df.to_dict("records")
    elif p.suffix.lower() == ".json":
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        yield from d if isinstance(d, list) else [d]
    elif p.suffix.lower() == ".txt":
        for i, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines()):
            if line.strip():
                yield {"id": str(i), "sentence": line}
    else:
        raise ValueError("지원 형식: JSON, JSONL, CSV, XLSX, TXT")


def import_metadata(con, path, dataset="사용자 메타데이터", split="미확인", progress=None):
    path = Path(path)
    digest = sha256(path)
    count = 0
    with con:
        for i, d in enumerate(read_records(path)):
            for test, parse in PARSERS:
                if test(d):
                    r = parse(d)
                    break
            else:
                raise ValueError(f"{i + 1}행: 지원하지 않는 구조")
            key = r["record_key"] or str(i)
            # 같은 ID의 다중 행 CSV에서도 글로스 구간을 잃지 않는다.
            key = f"{key}:{i}"
            cur = con.execute(
                """INSERT OR IGNORE INTO metadata_records
                (dataset,source_path,source_hash,record_key,korean_text,video_path,nms_json,split,video_url,original_record_path)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    dataset,
                    str(path.resolve()),
                    digest,
                    key,
                    r["korean_text"],
                    r["video_path"],
                    r["nms_json"],
                    split,
                    r.get("video_url", ""),
                    str(d.get("source_relative_path") or ""),
                ),
            )
            if cur.rowcount:
                rid = cur.lastrowid
                for s in r["segments"]:
                    con.execute(
                        """INSERT INTO gloss_segments(record_id,gloss,start,end,source_field,reliable)
                                VALUES (?,?,?,?,?,?)""",
                        (rid, s["gloss"], s["start"], s["end"], s["source_field"], s["reliable"]),
                    )
                count += 1
            if progress:
                progress(i + 1)
    return count


def search_metadata(con, word, limit=200):
    word = norm(word)
    # SQL LIKE 와일드카드 입력도 문자로 취급한다.
    esc = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    result = rows(
        con,
        """SELECT r.*,s.gloss,s.start,s.end,s.source_field,s.reliable
      FROM metadata_records r LEFT JOIN gloss_segments s ON s.record_id=r.id
      WHERE r.korean_text LIKE ? ESCAPE '\\' OR s.gloss LIKE ? ESCAPE '\\'
      ORDER BY CASE WHEN s.gloss=? THEN 0
        WHEN s.gloss LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,r.id,s.start LIMIT ?""",
        ("%" + esc + "%", "%" + esc + "%", word, esc + "%", limit),
    )
    for r in result:
        label = norm(r.get("gloss"))
        r["gloss_label_match"] = bool(re.fullmatch(re.escape(word) + r"(?:\d+#?)?", label))
        r["clip_candidate"] = bool(r["gloss_label_match"] and r["reliable"])
        r["semantic_match"] = "미확인 — 문맥·동작 검수 필요"
    return result
