"""JSONL 로딩 · 정제 · 분할."""
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

from .config import FIELDS, GLOSS_ITEM_KEYS


def _parse_gloss(raw):
    """여러 형태의 글로스 표기를 (글로스 리스트, 글로스별 (start,end) 리스트|None) 로 통일."""
    if raw is None:
        return [], None
    if isinstance(raw, str):
        return [g for g in raw.replace("/", " ").split() if g], None
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict):
            gk, sk, ek = GLOSS_ITEM_KEYS["gloss"], GLOSS_ITEM_KEYS["start"], GLOSS_ITEM_KEYS["end"]
            gl = [str(x.get(gk, "")).strip() for x in raw]
            times = [(x.get(sk), x.get(ek)) for x in raw]
            keep = [i for i, g in enumerate(gl) if g]
            return [gl[i] for i in keep], [times[i] for i in keep]
        return [str(g).strip() for g in raw if str(g).strip()], None
    return [], None


def load_jsonl(path):
    """레코드를 읽어 표준 dict 리스트로 반환하고, 정제 리포트도 함께 반환."""
    records, report = [], defaultdict(int)
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                report["json_error"] += 1
                continue
            ko = str(r.get(FIELDS["korean"], "") or "").strip()
            glosses, times = _parse_gloss(r.get(FIELDS["gloss"]))
            if not ko or not glosses:
                report["missing_korean_or_gloss"] += 1
                continue
            s, e = r.get(FIELDS["start"]), r.get(FIELDS["end"])
            if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e <= s:
                report["bad_time_range"] += 1
                s = e = None
            key = hashlib.md5((ko + "\t" + " ".join(glosses)).encode()).hexdigest()
            if key in seen:
                report["duplicate"] += 1
                continue
            seen.add(key)
            records.append({
                "korean": ko,
                "gloss": glosses,
                "gloss_times": times,
                "start": s, "end": e,
                "nms": r.get(FIELDS["nms"]),
                "source": r.get(FIELDS["source"]),
            })
            report["kept"] += 1
    return records, dict(report)


def split_records(records, ratios=(0.8, 0.1, 0.1), seed=42):
    """같은 한국어 문장이 train/test 양쪽에 들어가지 않도록 '문장 단위'로 분할."""
    by_ko = defaultdict(list)
    for r in records:
        by_ko[r["korean"]].append(r)
    keys = sorted(by_ko)
    random.Random(seed).shuffle(keys)
    n = len(keys)
    a, b = int(n * ratios[0]), int(n * (ratios[0] + ratios[1]))
    pick = lambda ks: [r for k in ks for r in by_ko[k]]
    return pick(keys[:a]), pick(keys[a:b]), pick(keys[b:])


def gloss_durations(records, default=0.8):
    """아바타 재생용 글로스별 평균 동작 시간(초).

    글로스 단위 시간이 있으면 그대로, 문장 단위 시간만 있으면 글로스 수로 균등 분배.
    """
    acc = defaultdict(list)
    for r in records:
        if r["gloss_times"]:
            for g, (s, e) in zip(r["gloss"], r["gloss_times"]):
                if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e > s:
                    acc[g].append(e - s)
        elif isinstance(r["start"], (int, float)) and isinstance(r["end"], (int, float)):
            d = (r["end"] - r["start"]) / len(r["gloss"])
            for g in r["gloss"]:
                acc[g].append(d)
    out = {g: round(sum(v) / len(v), 3) for g, v in acc.items()}
    out["__default__"] = round(sum(out.values()) / len(out), 3) if out else default
    return out


def save_json(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
