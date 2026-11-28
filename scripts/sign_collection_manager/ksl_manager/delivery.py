"""AI Hub 계열 좌표와 같은 프레임의 재생상태를 한 쌍으로 제공한다."""

import hashlib
import io
import json
import unicodedata
import zipfile
from pathlib import Path

import numpy as np

from .avatar_conversion import ah_avatar_convert
from .coordinate_export import safe_filename


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def delivery_files(avatar, word):
    word = unicodedata.normalize("NFC", safe_filename(word).removesuffix("_3D.json"))
    two, three, state = ah_avatar_convert(avatar, word)
    if three is None:
        raise ValueError("변환할 3D 좌표가 없습니다.")
    # 기존 구현팀 로더의 0~3 상태값을 유지하고 상세 출처는 별도로 남긴다.
    state["extended_point_status"] = state["point_status"]
    state["extended_point_status_codes"] = state["point_status_codes"]
    state["point_status"] = {
        k: np.where(np.asarray(v) == 4, 0, np.where(np.asarray(v) == 5, 1, np.asarray(v))).tolist()
        for k, v in state["extended_point_status"].items()
    }
    state["point_status_codes"] = {
        "0": "unknown/missing/synthetic; not observed",
        "1": "model or explicit manual; see extended provenance",
        "2": "bounded linear interpolation",
        "3": "nearest-depth estimate",
    }
    state["extended_point_status_codes"] = {
        **state["extended_point_status_codes"],
        "2": "bounded linear interpolation; source provenance preserved",
    }
    state["fps"] = three["fps"]
    state["n_frames"] = three["n_frames"]
    state["human_review"] = {"approved": False, "status": "not_recorded_in_collection_manager"}
    state["source_avatar_sha256"] = hashlib.sha256(json_bytes(avatar)).hexdigest()
    coordinate = f"좌표_3D/{word}.json"
    playback = f"재생상태/{word}.json"
    entry = {
        "word": word,
        "file": coordinate,
        "file_3d": coordinate,
        "playback_state": playback,
        "frames": three["n_frames"],
        "fps": three["fps"],
        "source_mode": "mediapipe_relative3d_estimate",
        "service_use_policy": "integration_candidate",
        "requires_review": True,
        "requires_motion_review": True,
        "human_review": state["human_review"],
        "source_avatar_sha256": state["source_avatar_sha256"],
    }
    manifest = {
        "coordinate_system": avatar["coord_system"],
        "items": [entry],
        "note": "모델 상대3D 추정 자료. 좌표 검수승인·수어 의미 검수·실제 아바타 재생 확인은 별도.",
    }
    files = {coordinate: three, playback: state, "자료목록.json": manifest}
    if two:
        files[f"좌표_2D/{word}.json"] = two
    return word, files


def delivery_archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, json_bytes(value))
        archive.write(
            Path(__file__).resolve().parents[1] / "tools/avatar_loader.py", "avatar_loader.py"
        )
        archive.writestr(
            "README.md",
            "좌표_3D와 재생상태의 같은 단어 JSON을 함께 사용하세요. 자료목록.json에 경로가 있습니다.\n모델 상대3D 추정 자료입니다. point_status=0은 미확인·누락·가상 자세, 2는 보간입니다. extended_point_status에 상세 출처가 남습니다.\n검수 전 연동 확인용이며 아바타 재생과 수어 의미 검수를 진행하세요.\n",
        )
    return output.getvalue()
