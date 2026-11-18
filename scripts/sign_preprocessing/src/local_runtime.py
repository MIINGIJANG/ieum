"""Local folder watcher and serialized pipeline operations (macOS/Linux)."""

import json
import os
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from pipeline import Pipeline, load, now, write

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
LABELS = {
    "registered": "등록",
    "extracted": "추출 완료",
    "review_pending": "검수 대기",
    "approved": "검수 승인",
    "rejected": "보류",
    "failed": "처리 실패",
}


class BusyError(RuntimeError):
    pass


@contextmanager
def exclusive_lock(path):
    import fcntl

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BusyError("다른 작업을 처리 중입니다. 완료 후 다시 시도하세요.")
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class StableFiles:
    """Wait for size/mtime to stop changing; not proof of a completed network transfer."""

    def __init__(self, seconds=8):
        self.seconds = seconds
        self.seen = {}

    def ready(self, paths, clock=None):
        clock = time.monotonic() if clock is None else clock
        active = set()
        ready = []
        for p in sorted(paths):
            try:
                s = p.stat()
            except FileNotFoundError:
                continue
            key = str(p.resolve())
            active.add(key)
            finger = (s.st_size, s.st_mtime_ns)
            prev = self.seen.get(key)
            if prev is None or prev[0] != finger:
                self.seen[key] = (finger, clock)
                continue
            if s.st_size > 0 and clock - prev[1] >= self.seconds:
                ready.append((p, finger))
        self.seen = {k: v for k, v in self.seen.items() if k in active}
        return ready


class Runtime:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.base = self.config_path.parent
        self.config = load(self.config_path)

        def rel(key):
            return (self.base / self.config[key]).resolve()

        self.input = rel("input_dir")
        self.workspace = rel("workspace_dir")
        if (
            self.input == self.workspace
            or self.input in self.workspace.parents
            or self.workspace in self.input.parents
        ):
            raise ValueError("입력 폴더와 작업 폴더는 서로 안에 둘 수 없습니다.")
        run = self.config.get("run_name", "기본작업")
        if Path(run).name != run or run in [".", ".."] or "/" in run or "\\" in run:
            raise ValueError("run_name 오류")
        self.root = self.workspace / run
        self.root.mkdir(parents=True, exist_ok=True)
        self.input.mkdir(parents=True, exist_ok=True)
        self.settings = self.config.get("processing", {})
        self.stop = threading.Event()
        self.current = None
        self.last_error = None
        self.stable = StableFiles(float(self.config.get("stable_seconds", 8)))
        if self.stable.seconds < 1:
            raise ValueError("stable_seconds는 1초 이상이어야 합니다.")
        self.attempts = {}
        self.scan_error = None
        # Fail early on incompatible checkpoints rather than repeating an error for every file.
        with exclusive_lock(self.root / ".pipeline.lock"):
            Pipeline(self.root, self.settings)

    def log(self, action, **fields):
        record = {"at": now(), "action": action, **fields}
        with (self.root / "watcher.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def pipeline(self):
        return Pipeline(self.root, self.settings)

    def snapshot(self):
        path = self.root / "pipeline.json"
        d = load(path) if path.exists() else {"items": {}}
        return {
            "input_dir": str(self.input),
            "workspace": str(self.root),
            "current": self.current,
            "last_error": self.last_error,
            "scan_error": self.scan_error,
            "watching": not self.stop.is_set(),
            "items": [
                {**r, "stage_label": LABELS.get(r["stage"], r["stage"])}
                for r in d["items"].values()
            ],
        }

    def videos(self):
        it = (
            self.input.rglob(("*"))
            if self.config.get(("recursive"), False)
            else self.input.iterdir()
        )
        return [
            p
            for p in it
            if p.is_file()
            and not p.is_symlink()
            and p.suffix.lower() in VIDEO_EXT
            and not any(x.startswith(".") for x in p.relative_to(self.input).parts)
        ]

    def tick(self, clock=None):
        paths = self.videos()
        names = {}
        duplicates = set()
        for p in paths:
            word = unicodedata.normalize("NFC", p.stem).strip()
            if word in names:
                duplicates.add(word)
            names[word] = p
        self.scan_error = (
            ("같은 단어 이름의 영상이 여러 개입니다: " + ", ".join(sorted(duplicates)))
            if duplicates
            else None
        )
        for path, finger in self.stable.ready(paths, clock):
            if self.stop.is_set():
                break
            word = unicodedata.normalize("NFC", path.stem).strip()
            key = str(path.resolve())
            if word in duplicates or self.attempts.get(key) == finger:
                continue
            try:
                with exclusive_lock(self.root / ".pipeline.lock"):
                    self.current = word
                    self.log("processing_started", word=word)
                    p = self.pipeline()
                    p.process([path])
                    stage = p.db["items"][word]["stage"]
                    self.last_error = (
                        p.db["items"][word].get("error") if stage == "failed" else None
                    )
                    self.log("processing_finished", word=word, stage=stage)
                    if stage != "failed":
                        self.maybe_ai(p)
                self.attempts[key] = finger
            except BusyError:
                continue
            except Exception as e:
                self.last_error = str(e)
                self.log("processing_error", word=word, error=str(e))
                self.attempts[key] = finger
            finally:
                self.current = None

    def maybe_ai(self, p):
        cfg = self.config.get("ai", {})
        if not cfg.get("enabled", False):
            return
        try:
            p.ai_summary(cfg.get("model", ""), os.environ.get("OPENAI_API_KEY", ""))
        except Exception as e:
            self.log("ai_summary_failed", error=type(e).__name__)

    def watch(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self.last_error = str(e)
                self.log("watcher_error", error=str(e))
            self.stop.wait(max(0.2, float(self.config.get("poll_seconds", 2))))

    def action(self, kind, body):
        with exclusive_lock(self.root / ".pipeline.lock"):
            p = self.pipeline()
            if kind in ["approve", "reject"]:
                p.decide(
                    body[("word")],
                    kind == ("approve"),
                    body.get(("reviewer"), ("")),
                    body.get(("note"), ("")),
                )
                return {"message": "검수 승인 완료" if kind == "approve" else "보류 처리 완료"}
            if kind == "correct":
                # No client-supplied filesystem path is opened.
                word = body["word"]
                d = body["data"]
                row = p.db["items"][word]
                candidate = p.pathof(row, "avatar").parent / "uploaded_correction.json"
                write(candidate, d)
                try:
                    p.replace_corrected(word, candidate)
                finally:
                    candidate.unlink(missing_ok=True)
                return {"message": "보정본 재검사 완료. 다시 승인해 주세요."}
            if kind == "export":
                return {
                    "message": "전달 ZIP 생성 완료",
                    "file": str(p.export_approved().relative_to(self.root)),
                }
            if kind == "review_zip":
                return {
                    "message": "검수 ZIP 생성 완료",
                    "file": str(p.review_zip().relative_to(self.root)),
                }
            if kind == "retry":
                word = body["word"]
                row = p.db["items"][word]
                if row["stage"] != "failed":
                    raise ValueError("실패한 자료만 재시도할 수 있습니다.")
                self.current = word
                try:
                    p.process([p.pathof(row, "source")])
                    self.maybe_ai(p)
                finally:
                    self.current = None
                return {"message": "재시도 완료. 상태를 확인하세요."}
            if kind == "ai":
                cfg = self.config.get("ai", {})
                key = body.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
                f = p.ai_summary(body.get("model") or cfg.get("model", ""), key)
                return {
                    ("message"): ("AI 검수 안내 생성 완료"),
                    ("file"): str(f.relative_to(self.root)),
                }
            raise ValueError("지원하지 않는 작업")
