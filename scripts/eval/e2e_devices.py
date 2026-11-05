"""화면 4종 자동 점검 (팀원 SignBridge 의 scripts/e2e_app.py 처럼): 휴대폰·태블릿 세로·태블릿 가로·PC 에서
첫 화면 → 예시 질문 → 답변·수어 블록·아바타가 나오는지, 가로로 넘치는 곳이 없는지 확인하고 화면을 찍어 둠.

  uvicorn api:app --port 8000 &                         # 서버 먼저
  python scripts/eval/e2e_devices.py --url http://localhost:8000 --out /tmp/e2e
  python scripts/eval/e2e_devices.py --dark              # 다크 모드로

실패한 항목이 있으면 종료 코드 1. 서버·브라우저가 필요해서 CI 가 아니라 배포 전 손으로 돌림.
"""
import argparse
import os
import sys
from pathlib import Path

DEVICES = {
    "phone": {"width": 390, "height": 844, "mobile": True},
    "tablet_portrait": {"width": 820, "height": 1180, "mobile": True},
    "tablet_landscape": {"width": 1180, "height": 820, "mobile": True},
    "desktop": {"width": 1440, "height": 900, "mobile": False},
}


def check(page, url, name, out, question):
    problems = []
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_selector("#boot", state="detached", timeout=180000)
    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
        problems.append("첫 화면이 가로로 넘침")
    page.screenshot(path=str(out / f"{name}_landing.png"))
    page.fill("#q", question)
    page.click("#go", no_wait_after=True)
    page.wait_for_selector("ol.glosses li", state="attached", timeout=90000)
    page.wait_for_timeout(2500)
    st = page.evaluate("""() => ({
        blocks: document.querySelectorAll('ol.glosses li').length,
        answer: (document.querySelector('#answer .text') || {}).textContent || '',
        avatar: !!document.querySelector('#stage.three'),
        overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    })""")
    if not st["blocks"]:
        problems.append("수어 블록 없음")
    if len(st["answer"].strip()) < 5:
        problems.append("답변 없음")
    if not st["avatar"]:
        problems.append("아바타가 안 보임")
    if st["overflow"]:
        problems.append("답변 화면이 가로로 넘침")
    page.screenshot(path=str(out / f"{name}_answer.png"), full_page=True)
    return problems, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/")
    ap.add_argument("--out", default="/tmp/e2e_devices")
    ap.add_argument("--question", default="낙하물은 어떻게 조심해요?")
    ap.add_argument("--dark", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    bad = 0
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                                # 파이썬 playwright 와 설치된 브라우저 버전이 다르면 직접 경로로
            b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/opt/pw-browsers/chromium"),
                                  args=["--headless", "--no-sandbox"])
        for name, d in DEVICES.items():
            ctx = b.new_context(viewport={"width": d["width"], "height": d["height"]}, is_mobile=d["mobile"],
                                has_touch=d["mobile"], color_scheme="dark" if a.dark else "light")
            page = ctx.new_page()
            try:
                problems, st = check(page, a.url, name, out, a.question)
            except Exception as e:                       # 시간 초과 등
                problems, st = [f"오류: {str(e).splitlines()[0][:120]}"], {}
            bad += bool(problems)
            print(f"{'✅' if not problems else '❌'} {name:<17} {d['width']}×{d['height']}  블록 {st.get('blocks', '-')}"
                  + (f"  → {', '.join(problems)}" if problems else ""))
            ctx.close()
        b.close()
    print(f"화면: {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
