"""동작 확인용 '가짜' 데이터 생성 (실제 AI Hub 데이터 아님!).

템플릿으로 만든 한국어-글로스 쌍이라 BLEU가 비정상적으로 높게 나온다.
파이프라인이 끝까지 도는지 확인하는 용도로만 쓰고, 성능 수치는 반드시 팀 JSONL로 다시 낸다.
'작업복'은 일부러 학습 데이터에서 빼서, 용어사전(lexicon) 슬롯으로 번역되는지 확인한다.
"""
import json
import random
import sys
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample/sample_ksl.jsonl")
rng = random.Random(0)

ITEMS = [("안전모", "안전모"), ("안전화", "안전화"), ("보안경", "보안경"), ("안전장갑", "장갑"),
         ("안전대", "안전대"), ("귀마개", "귀마개"), ("마스크", "마스크"), ("보호구", "보호구")]
PLACES = [("작업장", "작업장"), ("공사장", "공사장"), ("창고", "창고"), ("공장", "공장"), ("현장", "현장")]
HAZARDS = [("물체가 흩날릴", ["물체", "날다"]), ("떨어질", ["떨어지다"]),
           ("미끄러질", ["미끄럽다"]), ("화상을 입을", ["화상"]), ("소음이 클", ["소음", "크다"])]
MUST = [("해야 합니다", []), ("하세요", []), ("해야 한다", [])]


def josa(word, a, b):  # 받침 있으면 a, 없으면 b
    code = ord(word[-1]) - 0xAC00
    return a if 0 <= code < 11172 and code % 28 else b


def tpl(item, place, hz):
    (ik, ig), (pk, pg), (hk, hg) = item, place, hz
    m = rng.choice(MUST)[0]
    eul = josa(ik, "을", "를")
    yi = josa(ik, "이", "가")
    return rng.choice([
        (f"{pk}에서는 {ik}{eul} 반드시 착용{m}.", [pg, ig, "착용", "반드시", "필요"]),
        (f"{ik}{eul} 착용하지 않으면 다칠 수 있습니다.", [ig, "착용", "안하다", "다치다", "가능"]),
        (f"작업 전에 {ik} 상태를 확인{m}.", ["작업", "전", ig, "상태", "확인"]),
        (f"{hk} 위험이 있는 작업에서는 {ik}{eul} 사용{m}.", hg + ["위험", "있다", "작업", ig, "사용"]),
        (f"{ik}{yi} 손상되었으면 즉시 교체{m}.", [ig, "손상", "즉시", "교체"]),
        (f"{pk}에 들어가기 전에 {ik}{eul} 착용{m}.", [pg, "들어가다", "전", ig, "착용"]),
        (f"{pk}에서 {hk} 위험이 있으면 작업을 멈추세요.", [pg] + hg + ["위험", "있다", "작업", "멈추다"]),
    ])


seen, rows = set(), []
for _ in range(20000):                      # 가능한 조합을 대부분 뽑을 만큼 시도
    ko, gl = tpl(rng.choice(ITEMS), rng.choice(PLACES), rng.choice(HAZARDS))
    if ko in seen:
        continue
    seen.add(ko)
    t, glosses = round(rng.uniform(0, 2), 2), []
    for g in gl:  # 글로스 단위 시간 (아바타 타임라인 테스트용)
        d = round(rng.uniform(0.5, 1.1), 2)
        glosses.append({"gloss": g, "start": t, "end": round(t + d, 2)})
        t = round(t + d, 2)
    rows.append({"korean": ko, "gloss": glosses, "start": glosses[0]["start"], "end": t,
                 "nms": None, "source": f"SAMPLE/{len(rows):05d}"})

rows.append(rows[0])                                            # 중복 1건 (정제 확인용)
rows.append({"korean": "", "gloss": "작업", "source": "SAMPLE/bad"})  # 누락 1건

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{len(rows)} rows → {OUT}")
