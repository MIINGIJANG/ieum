"""기존 run.py once를 별도 작업 폴더에서 실행하는 선택적 어댑터."""

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path


def launch(
    video,
    pipeline_dir,
    workspace,
    python_path=None,
    display_name=None,
    hand_max_gap_sec=None,
    reuse_job=None,
):
    root = Path(pipeline_dir).expanduser().resolve()
    runner = root / "run.py"
    # 가상환경 Python의 심볼릭 링크를 실제 바이너리 경로로 풀면 가상환경을 잃는다.
    default_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = Path(python_path or default_python).expanduser().absolute()
    video = Path(video).expanduser().resolve()
    if not runner.is_file() or not python.is_file() or not video.is_file():
        raise ValueError("기존 파이프라인 폴더·Python 실행 파일·영상을 확인하세요.")
    job = (
        Path(workspace).resolve()
        / "jobs"
        / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    )
    (job / "input").mkdir(parents=True)
    shutil.copy2(video, job / "input" / video.name)
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    config.update(
        {
            "input_dir": str(job / "input"),
            "workspace_dir": str(job / "results"),
            "run_name": "collection_job",
            "stable_seconds": 1,
            "recursive": False,
        }
    )
    config["ai"] = {"enabled": False, "model": ""}
    runner_root = root
    engine_hash = None
    if hand_max_gap_sec is not None:
        from .interpolation import prepare_engine

        runner_root, engine_hash = prepare_engine(root, job, hand_max_gap_sec)
        runner = runner_root / "run.py"
        config.setdefault("processing", {})["hand_max_gap_sec"] = float(hand_max_gap_sec)
    if reuse_job is not None:
        from .interpolation import reuse_extracted

        reuse_extracted(reuse_job, job)
    cfg = job / "config.json"
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [str(python), str(runner), "once", "--config", str(cfg), "--no-browser"]
    # 같은 인터프리터의 자식 래퍼가 종료 코드를 파일로 보존한다.
    manifest = {
        "command": cmd,
        "cwd": str(runner_root),
        "pipeline_dir": str(root),
        "model_home": str(root / "models"),
        "hand_max_gap_sec": hand_max_gap_sec,
        "interpolation_engine_sha256": engine_hash,
        "reused_from_job": str(reuse_job) if reuse_job else None,
        "created_at": datetime.now().isoformat(),
        "display_name": display_name,
    }
    (job / "job.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (job / "run.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ksl_manager.preprocessing", str(job)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    threading.Thread(target=process.wait, daemon=True).start()
    (job / "pid.txt").write_text(str(process.pid), encoding="utf-8")
    return job


def job_status(job):
    job = Path(job)
    result = job / "result.json"
    return (
        json.loads(result.read_text(encoding="utf-8"))
        if result.exists()
        else {"state": "실행 중 또는 중단 — 로그 확인"}
    )


def worker(job):
    p = Path(job)
    manifest = json.loads((p / "job.json").read_text(encoding="utf-8"))
    try:
        environment = os.environ.copy()
        if manifest.get("model_home"):
            environment["KSL_MODEL_HOME"] = manifest["model_home"]
        r = subprocess.run(manifest["command"], cwd=manifest["cwd"], env=environment, check=False)
        output = {
            "state": "완료" if r.returncode == 0 else "실패",
            "returncode": r.returncode,
            "outputs": [str(x.relative_to(p)) for x in (p / "results").rglob("*.json")],
        }
    except OSError as exc:
        output = {"state": "실패", "error": str(exc)}
    (p / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    worker(sys.argv[1])


def reprocess_job(job, hand_max_gap_sec=0.4, display_name=None):
    job = Path(job).resolve()
    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    inputs = [
        p
        for p in (job / "input").iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}
    ]
    if len(inputs) != 1:
        raise ValueError("영상 한 개로 실행한 작업만 후처리할 수 있습니다.")
    root = manifest.get("pipeline_dir") or manifest["cwd"]
    return launch(
        inputs[0],
        root,
        job.parent.parent,
        manifest["command"][0],
        display_name or manifest.get("display_name"),
        hand_max_gap_sec,
        reuse_job=job,
    )
