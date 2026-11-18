"""Create an isolated Python 3.12 virtual environment; no system package edits."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def select_python():
    candidates = [shutil.which("python3.12"), sys.executable]
    for p in dict.fromkeys(x for x in candidates if x):
        if not Path(p).exists():
            continue
        try:
            version = subprocess.check_output(
                [
                    p,
                    ("-c"),
                    ('import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'),
                ],
                text=True,
            ).strip()
            if version == "3.12":
                return p
        except (OSError, subprocess.SubprocessError):
            pass
    raise RuntimeError(
        "Python 3.12가 필요합니다. python.org에서 Python 3.12를 설치한 뒤 다시 실행하세요."
    )


def main():
    python = select_python()
    env = ROOT / ".venv"
    exe = env / "bin/python"
    if not exe.exists():
        subprocess.run([python, "-m", "venv", str(env)], check=True)
    subprocess.run(
        [str(exe), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True
    )
    subprocess.run([str(exe), str(ROOT / "run.py"), "doctor"], check=True)
    print("\n설치 완료. 실행.command를 열거나 VS Code에서 .venv/bin/python을 선택하세요.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("설치 오류:", e)
        sys.exit(1)
