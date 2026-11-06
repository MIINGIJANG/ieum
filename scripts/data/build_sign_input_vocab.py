"""카메라 수어 입력(web/sign/) 의 '산업안전 단어만' 목록 만들기 → web/sign/domain.json

python scripts/data/build_sign_input_vocab.py

인식 모델(web/sign/models/meta.json, 팀원 SignBridge 의 낱말 인식 모델, 13,576개) 중에서
우리 서비스에 나오는 말만 고름 → 헷갈리는 후보가 줄어 정확도가 오름 (화면에서 끄고 켤 수 있음).
  - 동작사전 글로스(data/sign/motions/index.json), 단어표(sign_words.csv)·대체 표현의 글로스·말
  - 예시 질문·모범 답안·산업안전 핵심어·평가 질문에서 뽑은 명사·동사 (kiwipiepy)
  - 질문할 때 쓰는 말 (언제·어떻게·무엇·몇·어디·왜·필요 …)
모델 글로스('조심1', '먼지1#')는 번호를 뗀 말로 비교. 결과: {"labels": [모델 글로스...], "words": {글로스: 말}}
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ASK = ["언제", "어떻게", "무엇", "뭐", "몇", "어디", "왜", "누구", "필요", "방법", "이유", "괜찮다", "되다", "안되다", "하다",
       "해야", "있다", "없다", "알다", "모르다", "궁금", "질문", "물어보다", "가능", "못하다", "금지", "조심", "위험"]


def base(g):
    return re.sub(r"[\d#]+$", "", g)


def main():
    meta = json.loads((ROOT / "web/sign/models/meta.json").read_text(encoding="utf-8"))
    labels = [x for x in meta["labels"] if not x.startswith("<")]
    want = set(ASK)
    want |= {base(g) for g in json.loads((ROOT / "data/sign/motions/index.json").read_text(encoding="utf-8")) if ":" not in g}
    for ln in (ROOT / "data/sign/sign_words.csv").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith(("#", "말,")) and "," in ln:
            w, g = ln.rsplit(",", 1)
            want |= {base(g.strip()), w.lstrip("=").strip()}
    for ln in (ROOT / "data/sign/substitutes.csv").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith(("#", "말,")) and "," in ln:
            c = ln.split(",")
            want |= {c[0].strip(), *[base(x) for x in c[1].split()]}
    texts = list(json.loads((ROOT / "data/kosha/example_questions.json").read_text(encoding="utf-8")))
    for q, a in json.loads((ROOT / "data/kosha/answers.json").read_text(encoding="utf-8")).items():
        texts += [q, a.get("answer", ""), a.get("sign", "")]
    for f in ("eval_questions", "heldout_questions", "heldout2_questions", "heldout3_questions"):
        texts += [r["question"] for r in csv.DictReader(open(ROOT / f"data/eval/{f}.csv", encoding="utf-8-sig"))]
    texts += (ROOT / "data/kosha/domain_terms.txt").read_text(encoding="utf-8").split()
    from kiwipiepy import Kiwi
    kiwi = Kiwi()
    for t in texts:
        for tok in kiwi.tokenize(t):
            if tok.tag in ("NNG", "NNP", "XR"):
                want.add(tok.form)
            elif tok.tag.startswith(("VV", "VA")):
                want.add(tok.form + "다")
    picked = [x for x in labels if base(x) in want]
    # 수어 낱말 → 문서에 쓰는 말 (단어표를 거꾸로: 입다1 → 착용, 바꾸다1 → 교체) — 질문 찾기·바로 묻기에 씀
    rev = {}
    for ln in (ROOT / "data/sign/sign_words.csv").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith(("#", "말,")) and "," in ln:
            w, g = ln.rsplit(",", 1)
            w = w.lstrip("=").strip()
            toks = kiwi.tokenize(w)
            # 말이 명사 하나일 때만 ('곳에서는'·'떨어지' 같은 어절 조각은 빼고 '착용'·'교체'·'안전대' 만)
            if len(w) >= 2 and base(g.strip()) != w and not base(g.strip()).startswith(w) \
                    and not (len(w) <= 2 and base(g.strip()).startswith(w[:-1])) and len(toks) == 1 and toks[0].tag in ("NNG", "NNP") and toks[0].form == w:
                rev.setdefault(base(g.strip()), w)
    # 대체 표현을 거꾸로: 머리 + 보호 → 안전모
    combos = []
    for ln in (ROOT / "data/sign/substitutes.csv").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith(("#", "말,")) and "," in ln:
            c = ln.split(",")
            gs = [base(x) for x in c[1].split()]
            if len(gs) >= 2:
                combos.append({"words": gs, "as": c[0].strip()})
    out = {"_설명": "카메라 수어 입력의 산업안전 단어 목록 (scripts/data/build_sign_input_vocab.py 로 만듦)",
           "labels": picked, "words": {x: base(x) for x in picked},
           "to_doc": {k: v for k, v in rev.items() if k in {base(x) for x in picked}}, "combos": combos,
           # 질문 찾기 후보: 예시 질문 + 모범 답안이 있는 질문 + 개발용 평가 질문 (heldout 은 평가용이라 넣지 않음)
           "questions": list(dict.fromkeys(
               json.loads((ROOT / "data/kosha/example_questions.json").read_text(encoding="utf-8"))
               + list(json.loads((ROOT / "data/kosha/answers.json").read_text(encoding="utf-8")))
               + [r["question"] for r in csv.DictReader(open(ROOT / "data/eval/eval_questions.csv", encoding="utf-8-sig"))]))}
    (ROOT / "web/sign/domain.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"모델 {len(labels)}개 중 {len(picked)}개 (말 {len({base(x) for x in picked})}개)")
    missing = sorted(w for w in want if w not in {base(x) for x in labels} and len(w) > 1)
    print("모델에 없는 우리 말 (일부):", ", ".join(missing[:60]))


if __name__ == "__main__":
    main()
