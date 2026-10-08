"""팀 전처리 JSONL 구조 확인 → ksl/config.py 의 FIELDS 를 맞추는 용도.

python scripts/inspect_jsonl.py data/processed/ksl_safety.jsonl
"""
import json
import sys
from collections import Counter

path = sys.argv[1]
keys, types, n = Counter(), {}, 0
first = None
with open(path, encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        r = json.loads(line)
        n += 1
        first = first or r
        for k, v in r.items():
            keys[k] += 1
            types.setdefault(k, type(v).__name__)
print(f"레코드 {n}개\n")
print(f"{'key':<20}{'개수':>8}  type")
for k, c in keys.most_common():
    print(f"{k:<20}{c:>8}  {types[k]}")
print("\n첫 레코드:")
print(json.dumps(first, ensure_ascii=False, indent=2)[:1500])
print("\n→ ksl/config.py FIELDS 의 오른쪽 값을 위 key 이름으로 바꾸면 됨")
