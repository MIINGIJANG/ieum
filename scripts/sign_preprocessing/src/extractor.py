import csv
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision

from model_integrity import ensure_model


def ffmpeg_executable():
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


BASE = Path(
    os.environ.get("KSL_MODEL_HOME", str(Path(__file__).resolve().parent.parent / "models"))
)
MODELS = BASE / "models"
MODEL_URLS = {
    ("pose"): (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_l"
        "andmarker_full/float16/1/pose_landmarker_full.task"
    ),
    ("hand"): (
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_l"
        "andmarker/float16/1/hand_landmarker.task"
    ),
    ("face"): (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_l"
        "andmarker/float16/1/face_landmarker.task"
    ),
}

MODEL_SHA256 = {
    "pose": "5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1",
    "hand": "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1",
    "face": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
}


def ensure_models() -> None:
    """Fetch the pinned official MediaPipe task artifacts and verify their bytes."""
    for name, url in MODEL_URLS.items():
        ensure_model(MODELS / (name + ".task"), url, MODEL_SHA256[name])


HAND_EDGES = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (5, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    (9, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    (13, 17),
    (0, 17),
    (17, 18),
    (18, 19),
    (19, 20),
]
POSE_EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24)]


def xyz(points):
    return [[float(p.x), float(p.y), float(p.z)] for p in points]


def draw(frame, points, edges, color):
    h, w = frame.shape[:2]
    pts = [(round(p.x * w), round(p.y * h)) for p in points]
    for a, b in edges:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for p in pts:
        cv2.circle(frame, p, 2, color, -1)


def extract(src, folder):
    ensure_models()
    folder.mkdir(parents=True, exist_ok=True)
    normalized = folder / "_normalized.mp4"
    # 원본 시간 길이를 유지하며 고정 30fps로 변환합니다.
    subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-vf",
            "fps=30",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(normalized),
        ],
        check=True,
    )
    cap = cv2.VideoCapture(str(normalized))
    if not cap.isOpened():
        raise ValueError("영상을 열 수 없습니다")
    width, height = int(cap.get(3)), int(cap.get(4))
    expected = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_indices = {round((expected - 1) * q) for q in (0.1, 0.3, 0.5, 0.7, 0.9)}
    (folder / "samples").mkdir(exist_ok=True)
    writer = cv2.VideoWriter(
        str(folder / "_preview.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30, (width, height)
    )
    if not writer.isOpened():
        cap.release()
        raise ValueError("검수 영상 저장 실패")
    base = mp.tasks.BaseOptions
    mode = vision.RunningMode.VIDEO
    frames = []
    counts = {
        "pose_missing": 0,
        "face_missing": 0,
        "no_hands": 0,
        "one_hand": 0,
        "duplicate_hand_labels": 0,
    }
    try:
        with (
            vision.PoseLandmarker.create_from_options(
                vision.PoseLandmarkerOptions(
                    base_options=base(model_asset_path=str(MODELS / "pose.task")),
                    running_mode=mode,
                    num_poses=1,
                )
            ) as pose,
            vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=base(model_asset_path=str(MODELS / "hand.task")),
                    running_mode=mode,
                    num_hands=2,
                )
            ) as hand,
            vision.FaceLandmarker.create_from_options(
                vision.FaceLandmarkerOptions(
                    base_options=base(model_asset_path=str(MODELS / "face.task")),
                    running_mode=mode,
                    num_faces=1,
                )
            ) as face,
        ):
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                i = len(frames)
                if i in sample_indices:
                    if not cv2.imwrite(str(folder / "samples" / f"frame_{i:06d}.png"), frame):
                        raise ValueError("원본 표본 이미지 저장 실패")
                timestamp = round(i * 1000 / 30)
                image = mp.Image(
                    image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                )
                pr = pose.detect_for_video(image, timestamp)
                hr = hand.detect_for_video(image, timestamp)
                fr = face.detect_for_video(image, timestamp)
                row = {
                    "frame": i,
                    "timestamp_ms": timestamp,
                    "pose": None,
                    "pose_world": None,
                    "pose_visibility": None,
                    "face": None,
                    "hands": [],
                }
                if pr.pose_landmarks:
                    points = pr.pose_landmarks[0]
                    row["pose"] = xyz(points)
                    row["pose_world"] = xyz(pr.pose_world_landmarks[0])
                    row["pose_visibility"] = [float(p.visibility) for p in points]
                    draw(frame, points, POSE_EDGES, (0, 255, 0))
                else:
                    counts["pose_missing"] += 1
                if fr.face_landmarks:
                    row["face"] = xyz(fr.face_landmarks[0])
                    draw(frame, fr.face_landmarks[0], [], (255, 200, 0))
                else:
                    counts["face_missing"] += 1
                labels = []
                for j, points in enumerate(hr.hand_landmarks):
                    c = hr.handedness[j][0]
                    labels.append(c.category_name)
                    row["hands"].append(
                        {
                            "label": c.category_name,
                            "score": float(c.score),
                            "landmarks": xyz(points),
                            "world_landmarks": xyz(hr.hand_world_landmarks[j]),
                        }
                    )
                    draw(
                        frame,
                        points,
                        HAND_EDGES,
                        (0, 0, 255) if c.category_name == "Right" else (255, 0, 255),
                    )
                counts["no_hands"] += len(labels) == 0
                counts["one_hand"] += len(labels) == 1
                counts["duplicate_hand_labels"] += len(labels) != len(set(labels))
                # 좌우 예측이 중복되어도 손 데이터를 덮어쓰지 않고 모두 보존합니다.
                frames.append(row)
                cv2.putText(
                    frame,
                    f"frame {i} | {timestamp / 1000:.2f}s",
                    (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 255),
                    2,
                )
                writer.write(frame)
                if len(frames) % 300 == 0:
                    print(f"{src.name}: {len(frames)}/{expected}", flush=True)
    finally:
        cap.release()
        writer.release()
    if not frames or (expected and len(frames) != expected):
        raise ValueError(f"프레임 읽기 불완전: {len(frames)}/{expected}")
    metadata = {
        "schema": "mediapipe_tasks_raw_v1",
        "source_file": src.name,
        "mediapipe_version": mp.__version__,
        "fps": 30,
        "frame_count": len(frames),
        "width": width,
        "height": height,
        "duration_seconds": len(frames) / 30,
        "mirrored_by_code": False,
        "interpolated": False,
        "coordinates": {
            ("image"): (
                "x/y normalized to image; z model-specific relative depth, NOT common metric 3D"
            ),
            "pose_world": "estimated metres; origin midpoint of hips",
            "hand_world": "estimated metres; separate origin for EACH hand",
            "hand_labels": "raw model predictions; review side assignments using preview",
        },
        "missing": "null or empty hands list; not replaced by zero",
    }
    (folder / "landmarks.json").write_text(
        json.dumps({"metadata": metadata, "frames": frames}, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    report = dict(source=src.name, frames=len(frames), **counts)
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-y",
            "-i",
            str(folder / "_preview.mp4"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(folder / "check.mp4"),
        ],
        check=True,
    )
    # Preserve the exact 30fps extraction video for downstream frame matching.
    (folder / "_preview.mp4").unlink()
    return report


def run_batch(inputs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for i, src in enumerate(map(Path, inputs), 1):
        folder = output / f"{i:03d}_{src.stem}"
        try:
            print(f"[{i}/{len(inputs)}] {src.name}", flush=True)
            results.append({"status": "ok", "folder": folder.name, **extract(src, folder)})
        except Exception as e:
            results.append({"status": "error", "source": src.name, "error": str(e)})
            print(f"실패: {src.name}: {e}", flush=True)
    (output / "batch_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("완료:", sum(r["status"] == "ok" for r in results), "성공 /", len(results), "전체")
    return results


def longest_gap(flags):
    best = current = 0
    for detected in flags:
        current = 0 if detected else current + 1
        best = max(best, current)
    return best


def quality_summary(output):
    output = Path(output)
    batch = json.loads((output / "batch_report.json").read_text())
    summaries = []
    annotation = []
    for item in batch:
        if item["status"] != "ok":
            continue
        folder = output / item["folder"]
        data = json.loads((folder / "landmarks.json").read_text())
        fs = data["frames"]
        n = len(fs)
        fps = data["metadata"]["fps"]
        row = {"folder": folder.name, "source": item["source"], "frames": n, "fps": fps}
        signals = {
            "pose": [f["pose"] is not None for f in fs],
            "face": [f["face"] is not None for f in fs],
            "any_hand": [len(f["hands"]) > 0 for f in fs],
            "both_hands": [
                len(f["hands"]) == 2 and {h["label"] for h in f["hands"]} == {"Left", "Right"}
                for f in fs
            ],
        }
        for label in ("Left", "Right"):
            signals[label] = [sum(h["label"] == label for h in f["hands"]) == 1 for f in fs]
        for key, flags in signals.items():
            row[key + "_detected_frames"] = sum(flags)
            row[key + "_detection_pct"] = round(100 * sum(flags) / n, 3)
            row[key + "_max_gap_seconds"] = round(longest_gap(flags) / fps, 3)
        row["duplicate_hand_label_frames"] = sum(
            len(f["hands"]) != len({h["label"] for h in f["hands"]}) for f in fs
        )
        row["metric_type"] = "detection_availability_not_accuracy"
        summaries.append(row)
        for sample in sorted((folder / "samples").glob("*.png")):
            i = int(sample.stem.split("_")[-1])
            for group, indices in [
                ("pose", [11, 12, 13, 14, 15, 16]),
                ("Left", [0, 4, 8, 12, 16, 20]),
                ("Right", [0, 4, 8, 12, 16, 20]),
            ]:
                for k in indices:
                    annotation.append(
                        dict(
                            folder=folder.name,
                            frame=i,
                            group=group,
                            index=k,
                            visible="",
                            x_px="",
                            y_px="",
                            reference_length_px="",
                            image=f"{folder.name}/samples/{sample.name}",
                        )
                    )
    if summaries:
        with (output / "quality_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summaries[0]))
            w.writeheader()
            w.writerows(summaries)
    with (output / "annotation_template.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "folder",
            "frame",
            "group",
            "index",
            "visible",
            "x_px",
            "y_px",
            "reference_length_px",
            "image",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(annotation)
    (output / "quality_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2)
    )
    (output / "평가지표_읽어주세요.txt").write_text(
        (Path(__file__).resolve().parent.parent / "tools" / "원본좌표_평가지표.txt").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    return summaries


def evaluate_annotations(csv_path, output):
    output = Path(output)
    cache = {}
    seen = set()
    evaluated = []
    errors = []
    skipped = {"unannotated": 0, "not_visible": 0}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for line, r in enumerate(csv.DictReader(f), 2):
            if not r.get("visible", "").strip():
                skipped["unannotated"] += 1
                continue
            if r["visible"].strip() == "0":
                skipped["not_visible"] += 1
                continue
            try:
                if r["visible"].strip() != "1":
                    raise ValueError("visible은 0 또는 1")
                folder = r["folder"]
                group = r["group"]
                idx = int(r["index"])
                frame = int(r["frame"])
                path = (output / folder / "landmarks.json").resolve()
                if output.resolve() not in path.parents:
                    raise ValueError("잘못된 경로")
                if folder not in cache:
                    cache[folder] = json.loads(path.read_text())
                data = cache[folder]
                fs = data["frames"]
                meta = data["metadata"]
                if not 0 <= frame < len(fs):
                    raise ValueError("프레임 범위 오류")
                limit = 33 if group == "pose" else 21 if group in ("Left", "Right") else 0
                if not 0 <= idx < limit:
                    raise ValueError("관절 그룹/번호 오류")
                key = (folder, frame, group, idx)
                if key in seen:
                    raise ValueError("중복 정답 행")
                seen.add(key)
                x, y, ref = map(float, (r["x_px"], r["y_px"], r["reference_length_px"]))
                if not all(math.isfinite(v) for v in (x, y, ref)) or ref <= 0:
                    raise ValueError("좌표/기준 길이 오류")
                if not (0 <= x < meta["width"] and 0 <= y < meta["height"]):
                    raise ValueError("이미지 범위 밖 정답")
                fr = fs[frame]
                points = fr["pose"] if group == "pose" else None
                if group != "pose":
                    hands = [h for h in fr["hands"] if h["label"] == group]
                    if len(hands) == 1:
                        points = hands[0]["landmarks"]
                error = None
                if points is not None:
                    p = points[idx]
                    if all(math.isfinite(v) for v in p[:2]):
                        error = math.hypot(p[0] * meta["width"] - x, p[1] * meta["height"] - y)
                evaluated.append(
                    dict(
                        folder=folder,
                        frame=frame,
                        group=group,
                        index=idx,
                        pixel_error=error,
                        normalized_error=None if error is None else error / ref,
                    )
                )
            except Exception as e:
                errors.append({"line": line, "error": str(e)})
    if errors:
        (output / "annotation_errors.json").write_text(
            json.dumps(errors, ensure_ascii=False, indent=2)
        )
        raise ValueError(
            f"정답 CSV 오류 {len(errors)}개. annotation_errors.json 확인. 점수 생성 중단."
        )
    groups = {"all": evaluated}
    for group in ("pose", "Left", "Right"):
        groups[group] = [e for e in evaluated if e["group"] == group]
    scores = {}
    for group, es in groups.items():
        n = len(es)
        valid = [e["pixel_error"] for e in es if e["pixel_error"] is not None]
        scores[group] = {
            "evaluated_points": n,
            "matched_points": len(valid),
            "missing_or_ambiguous_prediction_points": n - len(valid),
            "mean_pixel_error_matched_only": sum(valid) / len(valid) if valid else None,
        }
        for t in (0.1, 0.2):
            scores[group][f"PCK_shoulder@{t}_pct"] = (
                100
                * sum(e["normalized_error"] is not None and e["normalized_error"] <= t for e in es)
                / n
                if n
                else None
            )
    report = {
        "scope": "manually annotated sample; 2D only",
        "normalizer": "manual 2D shoulder width",
        "skipped": skipped,
        "scores": scores,
        "details": evaluated,
    }
    (output / "accuracy_2d.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    )
    return report
