"""Load normalized BODY_25 / FACE_70 / HAND_21 clips, with mandatory validity/rest state."""

import json
import math
import unicodedata
from pathlib import Path


def _resolve(root, rel):
    """macOS(NFD)/Windows/Linux(NFC) 어디서 풀었든 한글 파일명을 찾는다."""
    p = root / rel
    if p.exists():
        return p

    def norm(s):
        return unicodedata.normalize("NFC", s)

    cur = root
    for part in Path(rel).parts:
        nxt = cur / part
        if not nxt.exists():
            nxt = next((c for c in cur.iterdir() if norm(c.name) == norm(part)), None)
            if nxt is None:
                raise FileNotFoundError(str(root / rel))
        cur = nxt
    return cur


def load_clip(package_root, word, *, allow_review_candidates=False):
    root = Path(package_root)
    manifest = json.loads(_resolve(root, "자료목록.json").read_text(encoding="utf-8-sig"))

    def norm(s):
        return unicodedata.normalize("NFC", s)

    entry = next((x for x in manifest["items"] if norm(x["word"]) == norm(word)), None)
    if entry is None:
        raise KeyError(word)
    if entry["service_use_policy"] == "review_only" and not allow_review_candidates:
        raise ValueError(
            f"{word}: 깊이 미확보 또는 글로스 변형 검토용 후보입니다. 검수 전 서비스 자동 적용을 막았습니다."
        )
    data = json.loads(_resolve(root, entry["file"]).read_text(encoding="utf-8-sig"))
    state = json.loads(_resolve(root, entry["playback_state"]).read_text(encoding="utf-8-sig"))
    n = data["n_frames"]
    parts = {}
    for key, count in [("pose", 25), ("face", 70), ("hand_right", 21), ("hand_left", 21)]:
        field = key + "_keypoints_3d"
        seq = data["landmarks"][field]
        mask = state["point_status"][field]
        if len(seq) != n or len(mask) != n:
            raise ValueError("프레임 수 불일치")
        if any(
            len(row) != count * 3
            or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in row)
            for row in seq
        ):
            raise ValueError("잘못된 좌표 배열")
        if any(len(row) != count or any(v not in [0, 1, 2, 3] for v in row) for row in mask):
            raise ValueError("잘못된 상태 배열")
        parts[key] = [[row[j : j + 3] for j in range(0, count * 3, 3)] for row in seq]
    return {
        "word": word,
        "fps": data["fps"],
        "frames": n,
        **parts,
        "point_status": state["point_status"],
        "hand_rest_weight": state["hand_rest_weight"],
        "face_enabled_frames": state["face_enabled_frames"],
        "requires_review": entry["requires_review"],
        "source_mode": entry["source_mode"],
        "coordinate_system": manifest["coordinate_system"],
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("folder")
    p.add_argument("word")
    p.add_argument("--allow-review-candidates", action="store_true")
    a = p.parse_args()
    clip = load_clip(a.folder, a.word, allow_review_candidates=a.allow_review_candidates)
    print(
        json.dumps(
            {
                k: v
                for k, v in clip.items()
                if k
                not in [
                    "pose",
                    "face",
                    "hand_right",
                    "hand_left",
                    "point_status",
                    "hand_rest_weight",
                    "face_enabled_frames",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
