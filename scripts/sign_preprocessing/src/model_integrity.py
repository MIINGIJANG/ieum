"""Atomic downloads with project-pinned SHA256, including existing cache files."""

import hashlib
import urllib.request
from collections.abc import Callable
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_model(
    path: Path, url: str, expected_sha256: str, retrieve: Callable = urllib.request.urlretrieve
) -> None:
    """Reject an altered cache or download; never publish a partial task file."""
    path = Path(path)
    if path.exists():
        if file_sha256(path) != expected_sha256:
            raise ValueError(f"{path.name}: 모델 해시 불일치. 캐시 파일을 확인하세요.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".download")
    try:
        retrieve(url, temp)
        if file_sha256(temp) != expected_sha256:
            raise ValueError(f"{path.name}: 다운로드 모델 해시 불일치")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
