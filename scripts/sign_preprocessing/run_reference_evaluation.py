"""Recreate frame-exact AI Hub clips and evaluate the production pipeline.

Requires locally held reference JSON and matching source videos; downloads no data.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import imageio_ffmpeg

from batch_v3 import video_info
from evaluate_coordinates import evaluate, nfc
from pipeline import Pipeline, sha, write


def run(reference: Path, source_videos: Path, output: Path) -> Path:
    """Keep inputs, settings and results separate; do not approve evaluation clips."""
    output.mkdir(parents=True, exist_ok=True)
    refs = sorted(reference.rglob("AIHub_참고좌표_2D.json"))
    if not refs:
        raise ValueError("정답 JSON을 찾지 못했습니다.")
    sources = {}
    for p in source_videos.rglob("*.mp4"):
        key = nfc(p.name)
        if key in sources:
            raise ValueError("같은 이름의 원본 영상이 여러 개입니다: " + key)
        sources[key] = p
    inputs = output / "input"
    inputs.mkdir(exist_ok=True)
    manifest = []
    for ref in refs:
        data = json.loads(ref.read_text(encoding="utf-8-sig"))
        meta = data["provenance"]
        word = nfc(data["word"])
        if Path(word).name != word or word in [".", ".."]:
            raise ValueError("단어명 오류")
        source = sources.get(nfc(meta["original_video_filename"]))
        if source is None:
            raise FileNotFoundError(meta["original_video_filename"])
        clip = inputs / (word + ".mp4")
        start = meta["original_start_frame_inclusive"]
        end = meta["original_end_frame_exclusive"]
        if end - start != data["n_frames"]:
            raise ValueError(word + ": 정답 구간 길이 오류")
        if not clip.exists():
            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-vf",
                    f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-crf",
                    "0",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    str(clip),
                ],
                check=True,
            )
        info = video_info(clip)
        if (info["frames"], info["width"], info["height"]) != (
            data["n_frames"],
            data["width"],
            data["height"],
        ) or abs(info["fps"] - float(data["fps"])) > 1e-6:
            raise ValueError(word + ": 영상과 정답의 프레임·크기·FPS 불일치")
        manifest.append(
            {
                "word": word,
                "source_video": source.name,
                "source_sha256": sha(source),
                "reference_sha256": sha(ref),
                "input_sha256": sha(clip),
                "start_frame_inclusive": start,
                "end_frame_exclusive": end,
            }
        )
    write(output / "input_manifest.json", manifest)
    p = Pipeline(output / "pipeline")
    p.process(sorted(inputs.glob("*.mp4")))
    failed = [r["word"] for r in p.db["items"].values() if r["stage"] == "failed"]
    if failed:
        raise ValueError("평가 입력 처리 실패 (누락 없이 중단): " + ", ".join(failed))
    # Record quality at each final signal-processing stage; conversion is not a fresh prediction.
    score, _ = evaluate(reference, p.root, p.root)
    score["pipeline_settings"] = p.settings
    score["code_signature"] = p.db["signature"]
    score["manual_correction_applied"] = False
    score["reference_confidence_sensitivity"] = {}
    for threshold in [0.3, 0.7]:
        other, _ = evaluate(reference, p.root, p.root, threshold)
        score["reference_confidence_sensitivity"][str(threshold)] = other["aggregate"]["hands"]
    score["converted_2d"] = converted_stage_scores(reference, p.root)
    write(output / "accuracy.json", score)
    print(
        json.dumps(
            {
                "word_count": score["word_count"],
                "clip_frames": score["clip_frames"],
                "hands": score["aggregate"]["hands"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return output / "accuracy.json"


def converted_stage_scores(reference: Path, pipeline_root: Path) -> dict:
    "Score converted image-plane joints with their availability masks, without approving data."
    import numpy as np

    import aihub_converter as converter
    from evaluate_coordinates import load, prepare, stats

    refs, _ = load(reference, "ref")
    raws, _ = load(pipeline_root, "raw")
    posts, _ = load(pipeline_root, "post")
    rows = []
    for word in sorted(refs):
        row = prepare(word, refs[word], raws[word], posts[word], 0.5)
        two, three, state = converter.ah_avatar_convert(posts[word], word)
        if two is None:
            raise ValueError(word + ": inverse-normalized 2D output missing")
        n = row["T"]
        parts = []
        masks = []
        for key, count, indices in [
            ("pose", 25, [2, 5, 3, 6, 4, 7]),
            ("hand_left", 21, list(range(21))),
            ("hand_right", 21, list(range(21))),
        ]:
            a = np.asarray(two[("landmarks")][key + ("_keypoints_2d")]).reshape(n, count, 3)[
                :, indices
            ]
            parts.append(a[:, :, :2])
            masks.append(a[:, :, 2] > 0)
        xy = np.concatenate(parts, axis=1)
        valid = np.concatenate(masks, axis=1) & np.isfinite(xy).all(2)
        row.update(
            post=xy,
            ap=valid,
            ep=np.linalg.norm(xy - row["xy"], axis=2) / row["denom"][:, None],
            common=row["ar"] & valid,
        )
        rows.append(row)
    return {
        "hands": stats(rows, "hands"),
        "pose": stats(rows, "pose"),
        ("note"): (
            "AIHub-like conversion after postprocessing; no human correction or rel"
            "ease approval applied"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--source-video-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.reference_dir, args.source_video_dir, args.output_dir)


if __name__ == "__main__":
    main()
