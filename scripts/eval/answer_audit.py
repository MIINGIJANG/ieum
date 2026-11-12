"""답변 품질 점검: 질문 목록을 서버에 실제로 물어보고 답·출처·수어 블록을 모아 표로 (docs/evaluation.md).

    python scripts/eval/answer_audit.py --url https://miingi-ieum.hf.space --out audit.json

- 질문: data/eval 의 문서 안 질문(정답 쪽·정답 낱말 포함) 전부 + 문서 밖 입력
- 재는 것: 답한 비율, 정답 쪽이 출처에 있는지, 정답 낱말이 답에 있는지, 답변 방식(llm·demo·extractive), 수어 블록 점검(끝맺음·회색 단어)
- 서버의 요청 한도(새 질문 1분 20개)에 맞춰 천천히 보냄. Claude 답변 모드면 질문당 약 3원
정답 낱말은 '답에 꼭 들어가야 하는 말' 하나라서, 없다고 다 틀린 답은 아님 → 표의 '낱말 없음' 은 사람이 읽어 확인.
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETS = ["eval_questions", "heldout_questions", "heldout2_questions", "heldout3_questions", "audit_questions"]


def post(url, body, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "ieum-audit"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:                      # 429(한도)·일시 오류 → 기다렸다 다시
            wait = 65 if "429" in str(e) else 5 * (i + 1)
            print(f"  다시 시도({e}) {wait}초 뒤", file=sys.stderr)
            time.sleep(wait)
    return {"mode": "error"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--out", default="audit.json")
    ap.add_argument("--pace", type=float, default=3.2, help="질문 사이 쉬는 시간(초)")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    rows = []
    for name in SETS:
        with open(ROOT / "data/eval" / f"{name}.csv", encoding="utf-8") as f:
            rows += [{**r, "set": name} for r in csv.DictReader(f)]
    with open(ROOT / "data/eval/out_of_scope_questions.csv", encoding="utf-8") as f:
        rows += [{"question": r["question"], "gold_page": "", "gold_keyword": "", "set": "out_of_scope"} for r in csv.DictReader(f)]
    if a.limit:
        rows = rows[:a.limit]
    out = []
    for i, r in enumerate(rows):
        t0 = time.time()
        d = post(a.url.rstrip("/") + "/query", {"text": r["question"], "prefetch": True})
        ms = round((time.time() - t0) * 1000)
        ans = d.get("answer_text") or ""
        flat = ans.replace(" ", "")
        sents = d.get("sentences") or []
        words = [w for s in sents for w in (s.get("words") or []) if w]
        out.append({**r, "mode": d.get("mode"), "answer_mode": d.get("answer_mode"), "answer": ans, "ms": ms,
                    "pages": [s.get("page") for s in d.get("sources") or []],
                    "page_ok": bool(r["gold_page"]) and int(r["gold_page"]) in [s.get("page") for s in d.get("sources") or []],
                    "kw_ok": bool(r["gold_keyword"]) and r["gold_keyword"].replace(" ", "") in flat,
                    "sign_text": d.get("sign_text") or "", "blocks": words,
                    "gloss": [g for s in sents for g in s.get("gloss") or []],
                    "method": sents[0].get("method") if sents else None,
                    "unk": [t for s in sents for t in (s.get("unverified_terms") or []) + (s.get("dropped_terms") or [])],
                    "debug": d.get("sign_debug")})
        print(f"{i + 1}/{len(rows)} {r['question'][:30]} → {d.get('answer_mode')} {ms}ms", file=sys.stderr)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if ms > 400:                                 # 캐시에 없던 질문만 한도에 세므로 그때만 쉼
            time.sleep(a.pace)
    ins = [o for o in out if o["set"] != "out_of_scope"]
    oos = [o for o in out if o["set"] == "out_of_scope"]
    ans = [o for o in ins if o["mode"] == "ask"]
    from collections import Counter
    print(json.dumps({
        "문서 안 질문": len(ins), "답함": len(ans), "정답 쪽이 출처에 있음": sum(o["page_ok"] for o in ans),
        "정답 낱말이 답에 있음": sum(o["kw_ok"] for o in ans), "답변 방식": dict(Counter(o["answer_mode"] for o in ins)),
        "문서 밖 입력": len(oos), "문서 밖인데 답함": sum(o["mode"] == "ask" for o in oos),
        "수어 블록 없음": sum(not o["blocks"] for o in ans),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
