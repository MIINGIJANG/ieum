"""Launch the local Streamlit application."""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def command(port=8502):
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(BASE / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Korean Sign Language collection manager")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument(
        "--check", action="store_true", help="Check required packages without starting the server"
    )
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required.")
    required = ["streamlit", "pandas", "numpy", "openpyxl", "pypdf", "dotenv", "requests"]
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        print("Missing packages: " + ", ".join(missing), file=sys.stderr)
        print("Install: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    if args.check:
        print("Dependencies OK")
        return 0
    try:
        return subprocess.run(command(args.port), cwd=BASE, check=False).returncode
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
