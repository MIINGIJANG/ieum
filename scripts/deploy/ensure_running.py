"""Hugging Face Space 가 멈춰 있으면(일시정지·잠자기·오류로 멈춤) 다시 켬. GitHub Actions 에서 HF_TOKEN 으로 실행.

python scripts/deploy/ensure_running.py [MIINGI/ieum]

- 무료 Space 는 오래 안 쓰면 잠들고, 주인·팀원이 일시정지할 수도 있음 → 화면에 "The space is paused" 만 나옴
- 실행 중·빌드 중이면 아무것도 안 함. 다시 켤 권한이 없으면(토큰 권한·요금 문제) 오류로 끝나서 Actions 에 빨갛게 보임
"""
import os
import sys

from huggingface_hub import HfApi

repo = sys.argv[1] if len(sys.argv) > 1 else "MIINGI/ieum"
api = HfApi(token=os.environ.get("HF_TOKEN"))
stage = api.get_space_runtime(repo).stage
print(f"{repo}: {stage}")
if stage in ("PAUSED", "SLEEPING", "STOPPED", "RUNTIME_ERROR", "BUILD_ERROR"):
    try:
        api.restart_space(repo)
    except Exception as e:                       # 권한 없음 등
        print(f"::error::Space 를 다시 켜지 못했습니다 ({stage}): {e}")
        sys.exit(1)
    print(f"다시 켬 → {api.get_space_runtime(repo).stage}")
