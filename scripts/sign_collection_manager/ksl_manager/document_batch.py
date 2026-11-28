"""문서 구간별 추출, 원문 대조, 재사용과 부분 실패 처리."""

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import requests

from .documents import candidates
from .llm import IncompleteResponseError, InvalidJSONResponseError

CACHE_VERSION = "document_terms_schema_v1"


def split_text(text, limit=1800, overlap=120):
    """원문을 유지하면서 짧은 중첩 구간으로 나눈다."""
    if limit < 200 or not 0 <= overlap < limit // 2:
        raise ValueError("문서 구간 크기와 중첩 길이를 확인하세요.")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + limit // 2, end)
            if newline >= 0:
                end = newline + 1
        if text[start:end].strip():
            chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def cache_path(folder, text, source, use_llm):
    identity = json.dumps(
        {
            "version": CACHE_VERSION,
            "text": text,
            "source": unicodedata.normalize("NFC", source),
            "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            "use_llm": use_llm,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return Path(folder) / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json")


def read_cache(path, text, source):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return None
        rows = data["candidates"]
        notices = data["notices"]
        if not isinstance(rows, list) or not isinstance(notices, list):
            return None
        if not all(isinstance(x, str) for x in notices):
            return None
        for row in rows:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(k), str) for k in ["word", "sense", "sentence", "source"]
            ):
                return None
            if (
                unicodedata.normalize("NFC", row["source"]) != unicodedata.normalize("NFC", source)
                or not row["sentence"]
                or row["sentence"] not in text
            ):
                return None
            if not row["word"] or re.sub(r"\s+", "", row["word"]) not in re.sub(
                r"\s+", "", row["sentence"]
            ):
                return None
        return [{**row, "source": source} for row in rows], notices
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cache(path, rows, notices):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CACHE_VERSION, "candidates": rows, "notices": notices}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        temp = Path(f.name)
        json.dump(data, f, ensure_ascii=False)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def collect_document_candidates(con, records, use_llm=False, cache_dir=None, progress=None):
    """완료 구간은 재사용하고 실패 구간만 남기며 정상 후보를 반환한다."""
    terms = [dict(r) for r in records if r.get("word")]
    texts = [r for r in records if not r.get("word")]
    tasks = []
    for row in texts:
        text, source = row.get("sentence", ""), row.get("source", "")
        if use_llm:
            tasks.extend((part, source) for part in split_text(text))
        elif text.strip():
            tasks.append((text, source))
    notices = []
    stats = {"total": len(tasks), "completed": 0, "failed": 0, "cache_hits": 0}
    blocked = False

    def extract_part(text, source, depth=0):
        path = cache_path(cache_dir, text, source, use_llm) if cache_dir and use_llm else None
        cached = read_cache(path, text, source) if path else None
        if cached is not None:
            stats["cache_hits"] += 1
            return cached
        local_notices = []
        try:
            result = candidates(con, text, source, use_llm, diagnostics=local_notices)
        except (IncompleteResponseError, InvalidJSONResponseError):
            if depth >= 2 or len(text) <= 400:
                raise
            parts = split_text(text, limit=max(200, len(text) // 2), overlap=40)
            result = []
            for part in parts:
                child, messages = extract_part(part, source, depth + 1)
                result.extend(child)
                local_notices.extend(messages)
        if path:
            try:
                write_cache(path, result, local_notices)
            except OSError:
                local_notices.append(
                    f"{source}: 후보는 읽었지만 재사용 파일을 저장하지 못했습니다."
                )
        return result, local_notices

    for i, (text, source) in enumerate(tasks):
        if progress:
            progress(i, len(tasks), source)
        if blocked:
            stats["failed"] += 1
            continue
        try:
            result, messages = extract_part(text, source)
            terms.extend(result)
            notices.extend(messages)
            stats["completed"] += 1
        except (ValueError, requests.RequestException) as exc:
            stats["failed"] += 1
            message = str(exc)
            notices.append(f"{source}: 해당 구간을 완료하지 못했습니다. {message[:300]}")
            if re.search(r"HTTP (401|403|429)\b", message):
                blocked = True
                notices.append(
                    "인증·권한·이용 한도로 남은 구간을 중단했습니다. 완료한 결과는 유지합니다."
                )
    if progress:
        progress(len(tasks), len(tasks), "")
    seen = set()
    unique = []
    for row in terms:
        key = tuple(row.get(k, "") for k in ["word", "sense", "sentence", "source"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return {"candidates": unique, "notices": notices, "stats": stats}
