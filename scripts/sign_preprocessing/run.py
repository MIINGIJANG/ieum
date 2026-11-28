#!/usr/bin/env python3
"""Run from VS Code or a local terminal. All default paths are project-relative."""

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))


def main():
    parser = argparse.ArgumentParser(description="이음 로컬 수어 데이터 파이프라인")
    parser.add_argument(
        "command", nargs="?", default="watch", choices=["watch", "once", "status", "doctor"]
    )
    parser.add_argument("--config", default=str(BASE / "config.json"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        import importlib.util

        modules = ["numpy", "cv2", "mediapipe", "imageio_ffmpeg"]
        bad = []
        print("Python:", sys.version.split()[0])
        for name in modules:
            found = importlib.util.find_spec(name) is not None
            print(name, ":", "있음" if found else "미설치")
            if not found:
                bad.append(name)
        if bad:
            return 1
        import imageio_ffmpeg

        print("영상 변환기:", imageio_ffmpeg.get_ffmpeg_exe())
        return 0
    from local_runtime import Runtime

    runtime = Runtime(args.config)
    if args.command == "status":
        print(json.dumps(runtime.snapshot(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "once":
        import time

        runtime.tick()
        time.sleep(runtime.stable.seconds)
        runtime.tick()
        return 1 if runtime.last_error else 0
    from local_runtime import exclusive_lock
    from local_web import ReviewServer

    with exclusive_lock(runtime.root / ".watcher.lock"):
        port = int(runtime.config.get("port", 8765))
        server = ReviewServer(("127.0.0.1", port), runtime, BASE / "web")
        address = f"http://127.0.0.1:{server.server_port}"
        worker = threading.Thread(target=runtime.watch, name="folder-watcher", daemon=False)
        worker.start()
        print("입력 폴더:", runtime.input, flush=True)
        print("관리 화면:", address, flush=True)
        print("종료: 터미널에서 Ctrl+C. 맥 잠자기 중에는 처리하지 않습니다.", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            runtime.stop.set()
            server.server_close()
            if runtime.current:
                print("현재 작업을 마무리하고 종료합니다.", flush=True)
            worker.join()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n종료했습니다. 저장한 진행 기록은 남아 있습니다.")
    except Exception as e:
        print("실행할 수 없습니다:", e, file=sys.stderr)
        sys.exit(1)
