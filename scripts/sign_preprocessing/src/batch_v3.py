"""Batch runner for matching MediaPipe raw JSON/NPY and source videos."""

import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
COORD_EXT = {".json", ".npy"}


def key_name(name):
    s = unicodedata.normalize("NFC", Path(name).stem)
    s = re.sub(r"^\d+[_ -]+", "", s)
    for _ in range(4):
        new = re.sub(r"(?i)(?:[_ -](?:landmarks|corrected|AIHub원본구간|원본구간))$", "", s)
        if new == s:
            break
        s = new
    return s.strip().casefold()


def input_key(p):
    if p.stem.lower() in {"landmarks", "coordinates"}:
        return key_name(p.parent.name)
    return key_name(p.name)


def decode_member(info):
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeError, ValueError):
            pass
    return unicodedata.normalize("NFC", name.replace("\\", "/"))


def ingest_upload(uploaded, dest, kind):
    """Extract only relevant files; never extract arbitrary archive contents."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    allowed = VIDEO_EXT if kind == "videos" else COORD_EXT
    saved = []
    ignored = 0
    total = 0
    limit = 20 * 1024**3

    def put(rel, raw):
        nonlocal ignored, total
        part = PurePosixPath(rel)
        if part.is_absolute() or ".." in part.parts or not part.parts:
            raise ValueError("안전하지 않은 압축 경로: " + rel)
        if any(x.startswith(".") or x == "__MACOSX" for x in part.parts):
            ignored += 1
            return
        if part.suffix.lower() not in allowed:
            ignored += 1
            return
        if kind == "videos" and (
            part.name.lower() in {"check.mp4", "preview.mp4"}
            or part.stem.lower().endswith(("_check", "_check_raw"))
        ):
            ignored += 1
            return
        if kind == "coordinates" and part.suffix.lower() == ".json":
            try:
                d = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError):
                ignored += 1
                return
            if not isinstance(d, dict) or not isinstance(d.get("frames"), list):
                ignored += 1
                return
        total += len(raw)
        if total > limit:
            raise ValueError("선택 파일 크기가 20GiB를 넘습니다. 나누어 올려 주세요.")
        p = dest.joinpath(*part.parts)
        if p.exists():
            raise ValueError("동일 경로 파일 중복: " + str(part))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        saved.append(p)

    for name, blob in uploaded.items():
        name = unicodedata.normalize("NFC", Path(name).name)
        if name.lower().endswith(".zip"):
            import io

            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    rel = decode_member(info)
                    part = PurePosixPath(rel)
                    if part.is_absolute() or ".." in part.parts:
                        raise ValueError("안전하지 않은 압축 경로: " + rel)
                    if part.suffix.lower() not in allowed or any(
                        x.startswith(".") or x == "__MACOSX" for x in part.parts
                    ):
                        ignored += 1
                        continue
                    if info.file_size > limit or total + info.file_size > limit:
                        raise ValueError("압축을 푼 선택 파일 크기가 너무 큽니다.")
                    put(Path(name).stem + "/" + rel, z.read(info))
        else:
            put(name, blob)
    return {"saved": len(saved), "ignored": ignored, "selected_bytes": total}


def find_inputs(root, kind):
    allowed = VIDEO_EXT if kind == "videos" else COORD_EXT
    out = []
    for p in Path(root).rglob("*"):
        if (
            not p.is_file()
            or p.suffix.lower() not in allowed
            or any(x.startswith(".") for x in p.relative_to(root).parts)
        ):
            continue
        if kind == "coordinates" and p.suffix.lower() == ".json":
            try:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(d, dict) or not isinstance(d.get("frames"), list):
                continue
        if kind == "videos" and (
            p.name.lower() in {"check.mp4", "preview.mp4"}
            or p.stem.lower().endswith(("_check", "_check_raw"))
        ):
            continue
        out.append(p)
    return sorted(out)


def make_plan(video_root, coord_root):
    vs = {}
    cs = {}
    for p in find_inputs(video_root, "videos"):
        vs.setdefault(input_key(p), []).append(p)
    for p in find_inputs(coord_root, "coordinates"):
        cs.setdefault(input_key(p), []).append(p)
    pairs = []
    issues = []
    for key in sorted(set(vs) | set(cs)):
        v = vs.get(key, [])
        c = cs.get(key, [])
        if len(v) != 1 or len(c) != 1:
            issues.append(
                {
                    "word": key,
                    "status": "unmatched_or_ambiguous",
                    "video_count": len(v),
                    "coordinate_count": len(c),
                    "videos": [str(p.relative_to(video_root)) for p in v],
                    "coordinates": [str(p.relative_to(coord_root)) for p in c],
                }
            )
            continue
        pairs.append(
            {
                "word": unicodedata.normalize("NFC", re.sub(r"^\d+[_ -]+", "", key)),
                "video": str(v[0]),
                "coordinates": str(c[0]),
            }
        )
    return pairs, issues


def video_info(p):
    import cv2

    cap = cv2.VideoCapture(str(p))
    try:
        if not cap.isOpened():
            raise ValueError("영상을 열 수 없습니다.")
        return {
            "fps": float(cap.get(cv2.CAP_PROP_FPS)),
            "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }
    finally:
        cap.release()


def prepare_video(video, coords, work, allow_fps_match=False):
    v = video_info(video)
    notes = []
    if coords.suffix.lower() == ".json":
        d = json.loads(coords.read_text(encoding="utf-8-sig"))
        meta = d.get("metadata", {})
        n = len(d["frames"])
        fps = meta.get("fps", meta.get("video_fps"))
        if fps is not None:
            fps = float(fps)
            if not math.isfinite(fps) or fps <= 0:
                raise ValueError("JSON FPS가 올바르지 않습니다.")
            if abs(v["fps"] - fps) > 0.05:
                if not allow_fps_match:
                    raise ValueError(
                        f"FPS 불일치: 영상 {v[('fps')]}, JSON {
                            fps
                        }. 추출 당시 영상을 넣거나 FPS 맞춤을 켜세요."
                    )
                import imageio_ffmpeg

                work.mkdir(parents=True, exist_ok=True)
                matched = work / "fps_matched.mp4"
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(video),
                        "-map",
                        "0:v:0",
                        "-vf",
                        f"fps={fps}",
                        "-an",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        str(matched),
                    ],
                    check=True,
                )
                video = matched
                v = video_info(video)
                notes.append("영상 FPS를 JSON에 맞춤. 타임라인의 실제 대응은 별도 확인 필요.")
        if meta.get("width") is not None and int(meta["width"]) != v["width"]:
            raise ValueError("영상과 JSON 가로 해상도가 다릅니다.")
        if meta.get("height") is not None and int(meta["height"]) != v["height"]:
            raise ValueError("영상과 JSON 세로 해상도가 다릅니다.")
    else:
        import numpy as np

        a = np.load(coords, mmap_mode="r", allow_pickle=False)
        if a.ndim != 3 or a.shape[1] != 543 or a.shape[2] not in (3, 4):
            raise ValueError("NPY 형식이 MediaPipe (T,543,3/4)가 아닙니다.")
        n = len(a)
    if v["fps"] <= 0 or not math.isfinite(v["fps"]):
        raise ValueError("영상 FPS를 확인할 수 없습니다.")
    # Batch evaluation requires exact frame counts, unlike the legacy runner tolerance.
    if n != v["frames"]:
        raise ValueError(
            f"프레임 수 불일치: 영상 {v['frames']}, 좌표 {n}. 자동으로 자르지 않습니다."
        )
    return video, v, notes


def run_batch(pairs, issues, engine, out, settings):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    records = []
    report = {
        "settings": settings,
        "matching_issues": issues,
        "results": records,
        "note": "후처리 실행 현황이며 정답 대비 정확도 점수가 아님",
    }
    for idx, pair in enumerate(pairs, 1):
        word = pair["word"]
        sub = out / word
        sub.mkdir(exist_ok=True)
        record = {
            "word": word,
            "input_video": Path(pair["video"]).name,
            "input_coordinates": Path(pair["coordinates"]).name,
        }
        try:
            v, info, notes = prepare_video(
                Path(pair["video"]),
                Path(pair["coordinates"]),
                sub / "_work",
                settings.get("allow_fps_match", False),
            )
            # Use a stable word filename; keep metadata's actual original source
            # name in the batch report.
            inp = sub / "_input"
            inp.mkdir(exist_ok=True)
            engine_video = inp / (word + v.suffix.lower())
            try:
                engine_video.symlink_to(v.resolve())
            except OSError:
                shutil.copy2(v, engine_video)
            fps = settings.get("out_fps") or info["fps"]
            cmd = [
                sys.executable,
                str(engine),
                str(engine_video),
                pair["coordinates"],
                "--out",
                str(sub),
                "--fps",
                str(fps),
                "--max-gap-sec",
                str(settings["max_gap_sec"]),
                "--min-cutoff",
                str(settings["min_cutoff"]),
                "--beta",
                str(settings["beta"]),
                "--rest-mode",
                settings["rest_mode"],
                "--rest-transition-frames",
                str(settings["rest_transition_frames"]),
            ]
            if settings.get("trim"):
                cmd.append("--trim")
            if not settings.get("make_check_video", True):
                cmd.append("--no-video")
            print(f"[{idx}/{len(pairs)}] {word} 처리 중", flush=True)
            with (sub / "실행로그.txt").open("w", encoding="utf-8") as log:
                r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
            if r.returncode:
                raise RuntimeError("후처리 실패. 해당 단어의 실행로그.txt를 확인하세요.")
            if not (sub / (word + "_avatar.json")).is_file():
                raise RuntimeError("결과 JSON이 생성되지 않았습니다.")
            record.update(
                status="success",
                source_video_info=info,
                notes=notes,
                avatar_json=str((sub / (word + "_avatar.json")).relative_to(out)),
            )
            print("  완료", flush=True)
        except Exception as e:
            record.update(status="failed", error=str(e))
            print("  건너뜀:", e, flush=True)
        finally:
            # Remove temporary links/video copies; preserve result and per-word logs only.
            shutil.rmtree(sub / "_input", ignore_errors=True)
            shutil.rmtree(sub / "_work", ignore_errors=True)
        records.append(record)
        report["counts"] = {
            "success": sum(x["status"] == "success" for x in records),
            "failed": sum(x["status"] == "failed" for x in records),
            "matching_issues": len(issues),
            "planned": len(pairs),
        }
        (out / "일괄처리결과.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not pairs:
        report[("counts")] = {
            ("success"): 0,
            ("failed"): 0,
            ("matching_issues"): len(issues),
            ("planned"): 0,
        }
        (out / "일괄처리결과.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("처리 결과:", report["counts"], flush=True)
    return report
