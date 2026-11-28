"""전처리 결과 파일과 검수 후보를 UI에서 찾기 쉽게 묶는다."""

import io
import json
import zipfile
from pathlib import Path

LABELS = {
    "long_internal_hand_gap": "손 좌표 누락",
    "overlapping_hand_predictions": "양손 겹침 후보",
    "duplicate_raw_hand_labels": "원본 손 라벨 중복",
    "wrist_jump": "손목 급이동 후보",
    "hand_side_assignment_candidate": "좌우 손 배정 후보",
    "shoulder_missing": "어깨 누락",
    "synthetic_rest": "가상 휴지 자세",
    "very_short_clip": "아주 짧은 구간",
}
PARTS = {"left_hand": "왼손", "right_hand": "오른손", "both": "양손", "pose": "몸", "all": "전체"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def result_items(job):
    job = Path(job).resolve()
    found = []
    for avatar in sorted((job / "results/collection_job/items").glob("*/processed/*_avatar.json")):
        if not avatar.resolve().is_relative_to(job):
            continue
        folder = avatar.parent.parent
        stem = avatar.name.removesuffix("_avatar.json")
        audit = folder / "audit.json"
        report = avatar.with_name(stem + "_report.json")
        item = {
            "stem": stem,
            "avatar": avatar,
            "audit": audit if audit.is_file() else None,
            "report": report if report.is_file() else None,
            "video": folder / "raw/_normalized.mp4",
            "check": avatar.with_name(stem + "_check.mp4"),
            "audit_data": read_json(audit) if audit.is_file() else {},
            "report_data": read_json(report) if report.is_file() else {},
        }
        item["events"] = review_events(item["audit_data"])
        found.append(item)
    return found


def review_events(audit):
    fps = float(audit.get("fps") or 30)
    rows = []
    for event in audit.get("events", []):
        for start, end in event.get("frame_ranges_end_exclusive", []):
            rows.append(
                {
                    "확인 항목": LABELS.get(event["code"], event["code"]),
                    "부위": PARTS.get(event.get("part"), event.get("part", "")),
                    "시작(초)": round(start / fps, 3),
                    "끝(초)": round(end / fps, 3),
                    "시작 프레임": start,
                    "끝 프레임(미포함)": end,
                    "확인할 내용": event.get("detail", ""),
                }
            )
    return sorted(rows, key=lambda r: r["시작 프레임"])


def review_bundle(item, word):
    """수동 보정에는 추출 당시 영상과 후처리 좌표를 함께 제공한다."""
    files = [
        (item["avatar"], word + "_avatar.json"),
        (item["video"], word + ".mp4"),
        (item.get("audit"), "audit.json"),
        (item.get("report"), word + "_report.json"),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        total = 0
        for source, name in files:
            if not source or not Path(source).is_file():
                continue
            size = Path(source).stat().st_size
            total += size
            if total > 200 * 1024 * 1024:
                raise ValueError(
                    "검수 묶음이 200MB를 초과합니다. 결과 폴더에서 파일을 직접 가져오세요."
                )
            archive.write(source, name)
    return buffer.getvalue()
