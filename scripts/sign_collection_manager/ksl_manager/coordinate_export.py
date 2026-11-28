"""원본 ID와 시간 구간이 일치하는 실제 좌표를 내보낸다. 3D를 우선하고 2D는 구분한다."""

import json
import math
import re
import tarfile
import unicodedata
from pathlib import Path


class CoordinateUnavailable(ValueError):
    """원하는 구간의 좌표를 확보하지 못함."""


def source_id(row):
    original = row.get("original_record_path") or ""
    if original:
        return Path(original).stem
    return re.sub(r":\d+$", "", row["record_key"]).removesuffix(".json")


def finite_number(value):
    if isinstance(value, bool):
        raise ValueError("숫자가 아닌 값")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("유한한 숫자가 아님")
    return number


def _build_coordinates(row, data, provenance, dimension):
    """출처 ID·실제 깊이·시간 범위·프레임 형태를 검사하고 구간을 자른다."""
    expected = source_id(row)
    meta = data.get("metadata") or {}
    actual = str(data.get("id") or meta.get("source_id") or meta.get("id") or "")
    if actual != expected:
        raise CoordinateUnavailable("원본 ID가 일치하지 않습니다.")
    try:
        fps = finite_number(data.get("fps") or meta.get("video_fps"))
        start, end = finite_number(row["start"]), finite_number(row["end"])
        if fps <= 0 or not 0 <= start < end or not row.get("reliable"):
            raise ValueError("시간 정보 없음")
    except (ValueError, TypeError, KeyError) as exc:
        raise CoordinateUnavailable("확인 가능한 FPS와 실제 시작·종료 시간이 필요합니다.") from exc
    first, last = math.floor(start * fps), math.ceil(end * fps)
    if "clips" in data:
        # 단어 클립에는 앞뒤 여유 프레임이 있을 수 있다. 원본 프레임 번호로 자른다.
        options = [c for c in data["clips"] if c.get("gloss") == row.get("gloss")]
    else:
        options = [{"landmarks": data.get("landmarks", {}), "frame_start": 0}]
    for candidate in options:
        try:
            offset = int(candidate.get("frame_start", 0))
            lo, hi = first - offset, last - offset
            landmarks = {
                k: v
                for k, v in candidate.get("landmarks", {}).items()
                if k.endswith("_keypoints_" + dimension) and isinstance(v, list) and v
            }
            if not landmarks or lo < 0 or hi <= lo:
                continue
            if any(hi > len(frames) for frames in landmarks.values()):
                continue
            cut = {k: v[lo:hi] for k, v in landmarks.items()}
            depth_exists = False
            for frames in cut.values():
                width = len(frames[0])
                if width == 0 or width % 3 or any(len(frame) != width for frame in frames):
                    raise ValueError("좌표 배열 크기 불일치")
                for frame in frames:
                    numbers = [finite_number(x) for x in frame]
                    depth_exists |= any(z != 0 for z in numbers[2::3])
            if dimension == "3d" and not depth_exists:
                continue
        except (TypeError, ValueError, IndexError):
            continue
        return {
            "metadata": {
                "id": expected,
                "source_id": expected,
                "gloss": row["gloss"],
                "video_fps": fps,
                "frame_count": last - first,
                "source_start": start,
                "source_end": end,
                "original_frame_start": first,
                "original_frame_end_exclusive": last,
                "dataset": row.get("dataset", ""),
                "split": row.get("split", "미확인"),
                "coordinate_type": "original_" + dimension,
                "coordinate_layout": "x,y,z" if dimension == "3d" else "x,y,confidence",
                "depth_available": dimension == "3d",
                "coordinate_system": "원본 좌표계 보존",
                "units": "원본 단위 미확인",
                "source_file": provenance,
                "semantic_review": "미확인",
            },
            "korean_text": row.get("korean_text", ""),
            "sign_script": [{"gloss_id": row["gloss"], "start": 0, "end": (last - first) / fps}],
            "landmarks": cut,
            "camera_parameter": data.get("camera_parameter")
            or data.get("landmarks", {}).get("camera_parameter", {}),
        }
    raise CoordinateUnavailable(
        f"해당 원본·단어 구간을 모두 포함하는 {dimension.upper()} 좌표가 없습니다."
    )


def build_3d(row, data, provenance):
    return _build_coordinates(row, data, provenance, "3d")


def build_2d(row, data, provenance):
    return _build_coordinates(row, data, provenance, "2d")


def locate_3d(row, roots, progress=None):
    return _locate(row, roots, progress, allow_2d=False)


def locate_coordinates(row, roots, progress=None):
    """모든 출처에서 3D를 먼저 확인하고, 없을 때만 보유 2D를 반환한다."""
    return _locate(row, roots, progress, allow_2d=True)


def _locate(row, roots, progress=None, allow_2d=False):
    """로컬 JSON을 먼저 보고, 일치 ID의 압축 보관 파일만 메모리로 읽는다."""
    ident = source_id(row)
    sources = []
    direct = Path(row.get("source_path") or "")
    if direct.is_file() and direct.suffix.lower() == ".json":
        sources.append(direct)
    archives = []
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = unicodedata.normalize("NFC", path.name)
            if name == ident + ".json":
                sources.append(path)
            elif name.endswith(".tar.gz"):
                archives.append(path)
    fallback = None
    checked = []

    def examine(data, provenance):
        nonlocal fallback
        try:
            return build_3d(row, data, provenance)
        except CoordinateUnavailable as exc:
            checked.append(str(exc))
        if allow_2d and fallback is None:
            try:
                fallback = build_2d(row, data, provenance)
            except CoordinateUnavailable:
                pass
        return None

    for path in dict.fromkeys(sources):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            result = examine(data, path.name)
            if result is not None:
                return result
        except (CoordinateUnavailable, ValueError, OSError):
            continue
    # 먼저 발견한 2D가 있어도 나머지 출처의 3D 확인을 마친다.
    for archive in sorted(set(archives), key=lambda p: ("extra" in p.name, str(p))):
        if progress:
            progress(f"{archive.name}에서 원본 좌표 확인 중…")
        try:
            with tarfile.open(archive, "r|gz") as tar:
                for member in tar:
                    if not member.isfile() or Path(member.name).name != ident + ".json":
                        continue
                    if member.size > 200 * 1024 * 1024:
                        checked.append("원본 JSON이 200MB를 초과함")
                        break
                    f = tar.extractfile(member)
                    data = json.load(f)
                    result = examine(data, archive.name + "::" + member.name)
                    if result is not None:
                        return result
                    break
        except (tarfile.TarError, OSError, ValueError) as exc:
            checked.append(f"{archive.name}: {type(exc).__name__}")
    if fallback is not None:
        return fallback
    detail = " ".join(dict.fromkeys(checked))
    raise CoordinateUnavailable("보유 검색 폴더에서 해당 구간의 좌표를 찾지 못했습니다. " + detail)


def download_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def safe_filename(gloss, dimension="3d"):
    if dimension not in ("3d", "2d"):
        raise ValueError("지원하지 않는 좌표 차원입니다.")
    word = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(gloss)).strip(" .") or "수어"
    return word[:100] + "_" + dimension.upper() + ".json"
