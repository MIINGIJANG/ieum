"""배포된 사이트의 /eval 로 환각 방지 평가 (.github/workflows/eval-live.yml).

  python scripts/eval/live_eval.py https://miingi-ieum.hf.space

/eval 은 처음 열면 뒤에서 평가를 시작하고, 다시 열면 결과를 줌 (서버가 다시 켜질 때까지 같은 결과).
결과를 $GITHUB_STEP_SUMMARY 에 쓰고, 문서 밖 입력에 답한 것(환각)이 있으면 실패(종료 코드 1).
"""
import json
import os
import sys
import time
import urllib.request


def get(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main(base):
    base = base.rstrip("/")
    for _ in range(20):                           # 사이트가 잠들어 있으면 깨어날 때까지 (최대 약 10분)
        try:
            get(f"{base}/health", 60)
            break
        except Exception as e:
            print("사이트 켜지는 중…", e)
            time.sleep(30)
    r = None
    for _ in range(40):                           # 평가 끝날 때까지 (최대 약 10분)
        r = get(f"{base}/eval")
        if r.get("상태") == "완료":
            break
        print(r.get("상태"))
        time.sleep(15)
    if not r or r.get("상태") != "완료":
        print("::error::평가가 끝나지 않았습니다")
        return 1
    if "오류" in r or "요약" not in r:
        print(f"::error::평가 중 오류: {r.get('오류', r)}")
        return 1
    s = r["요약"]
    leaked = s.get("새어 나간 입력", [])
    lines = ["## 사이트 환각 방지 평가", "",
             f"- Claude 답변 모드: {'켜짐' if r.get('Claude 답변 모드') else '꺼짐 (규칙 기반)'}"]
    lines += [f"- {k}: {v}" for k, v in s.items() if k not in ("새어 나간 입력", "답하지 못한 문서 안 질문")]
    lines += [f"- 답하면 안 되는데 답한 입력: {', '.join(leaked) if leaked else '없음 ✅'}",
              f"- 답하지 못한 문서 안 질문: {', '.join(s.get('답하지 못한 문서 안 질문', [])) or '없음'}"]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if leaked:
        print(f"::error::문서 밖 입력에 답함 (환각): {leaked}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "https://miingi-ieum.hf.space"))
