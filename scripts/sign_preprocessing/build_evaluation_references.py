"""Rebuild local reference clips from a frame manifest; redistribute no coordinates."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from pipeline import write


def build(label_dir: Path, manifest_path: Path, output: Path) -> int:
    """Slice original AI Hub labels with exact, disclosed zero-based frame ranges."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    labels = {}
    for p in label_dir.rglob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        if "metadata" in d and "landmarks" in d:
            labels[d["metadata"].get("id", p.stem)] = d
    for item in manifest["clips"]:
        source = labels[item["source_id"]]
        s = item["start_frame"]
        e = item["end_frame_exclusive"]
        if source["metadata"]["video_fps"] != item["fps"]:
            raise ValueError("FPS mismatch")
        fields = [
            "pose_keypoints_2d",
            "face_keypoints_2d",
            "hand_left_keypoints_2d",
            "hand_right_keypoints_2d",
        ]
        arrays = {k: source["landmarks"][k][s:e] for k in fields}
        if any(len(v) != e - s for v in arrays.values()):
            raise ValueError("Reference frame mismatch")
        word = item["word"]
        if Path(word).name != word or word in [".", ".."]:
            raise ValueError("Invalid word")
        data = {
            "schema": "AIHub_same_video_reference_2d",
            "word": word,
            "fps": item["fps"],
            "width": item["width"],
            "height": item["height"],
            "n_frames": e - s,
            "landmarks": arrays,
            "provenance": {
                "source_id": item["source_id"],
                "original_video_filename": item["source_id"] + "_F.mp4",
                "original_start_frame_inclusive": s,
                "original_end_frame_exclusive": e,
                "gloss": item["gloss"],
            },
        }
        write(output / word / "AIHub_참고좌표_2D.json", data)
    return len(manifest["clips"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label-dir", type=Path, required=True)
    p.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parent / "evaluation_manifest.json",
    )
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    print("참고 라벨 생성:", build(a.label_dir, a.manifest, a.output_dir))


if __name__ == "__main__":
    main()
