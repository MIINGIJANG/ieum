"""표의 한 줄 → 읽을 수 있는 문장 (Claude 없이 답할 때 씀, ksl/rag.py 추출식).

문서의 표는 글로 뽑으면 칸이 뒤섞여 '안전화 물체의 낙하…' 처럼 나온다. 표 구조(data/kosha/*.layout.json)를 알고 있으므로
줄마다 '작업 → 보호구', '종류 → 설명' 을 문장으로 만든다. 표에 적힌 말만 쓰고 내용을 더하지 않는다 (조사·끝맺음만 붙임).
"""
import re

_HANJA = re.compile(r"\s*\([一-鿿]+\)")
_WEAR = re.compile(r"(모|복|화|장갑|마스크|안경|보안면|보호구|호흡기|안전대|두건|앞치마|덮개|옷|귀마개|보호대)$")


def has_final(word):
    """마지막 글자에 받침이 있는지 (한글이 아니면 숫자·영문 발음으로 어림)."""
    c = word.rstrip(" )]」』\"'")[-1:] or ""
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28 != 0
    return c in "0136780LMNRlmnr"


def josa(word, pair):
    """pair: '은는' | '을를' | '이가' — 받침에 맞는 조사. 끝의 괄호 설명은 빼고 판단 ('귀마개 2종(EP-2)' → 종)."""
    core = re.sub(r"\s*\([^()]*\)$", "", word) or word
    return word + (pair[0] if has_final(core) else pair[1])


def clean(cell):
    t = _HANJA.sub("", cell or "")
    t = re.sub(r"\s*※.*$", "", t)                       # 칸 안의 덧붙임 설명
    t = re.sub(r"\s*\[[\d.]+\s*참조\]", "", t)
    t = t.replace("․", "·").replace("･", "·").replace("ㆍ", "·")
    return re.sub(r"\s+", " ", t).strip(" ,")


def _head(cell):
    return re.sub(r"\s", "", cell or "")


def work_gear(work, gear):
    """'○○ 작업' + '보호구' → '○○ 작업에는 보호구를 착용합니다.'"""
    work, gear = clean(work), clean(gear)
    if not work or not gear:
        return None
    m = re.match(r"^(.*?)\s*1\.\s", work)                  # '고열작업 1. 용광로… 2. …' → 앞의 이름만
    if m and m.group(1):
        work = m.group(1)
    elif m:                                                # 이름 없이 '1. … 2. …' → 항목마다 한 문장 (끝이 잘린 항목은 뺌)
        items = [w.strip() for w in re.split(r"\s*\d\.\s+", work) if w.strip()]
        outs = [x for w_ in items if re.search(r"(작업|업무|경우|장소)$", w_) and (x := work_gear(w_, gear))]
        return " ".join(outs) or None
    if len(work) > 120 or len(gear) > 80 or re.search(r"(하거나|한다|할것|하여야|지급)", gear.replace(" ", "")):
        return f"{work}에 필요한 보호구: {gear}." if len(work) <= 120 else None   # 문장으로 만들기 어려운 줄은 표의 말 그대로
    items = [re.sub(r"\s*\(.*?\)$|\s*등$", "", x).strip() for x in re.split(r"[,·]| 또는 ", gear)]
    verb = "착용합니다" if all(_WEAR.search(x) for x in items if x) else "사용합니다"
    where = "에서는" if work.endswith("장소") else "에는"
    return f"{work}{where} {josa(gear, '을를')} {verb}."


def term_desc(term, desc):
    """'정전기안전화' + '…방지하기 위한 것' → '정전기안전화는 …방지하기 위한 것입니다.'"""
    term, desc = clean(term), clean(desc)
    if not term or not desc or len(desc) < 8 or len(term) > 30 or re.search(r"(?:[가-힣] ){2,}", term + " "):   # '벨 트 식' 처럼 벌려 쓴 말은 뺌
        return None
    desc = desc.rstrip(".")
    if desc.endswith("장소"):
        return f"{josa(term, '은는')} {desc}에서 사용합니다."
    if re.search(r"(다|음|함)$", desc):                     # 이미 문장
        return f"{josa(term, '은는')} {desc}."
    return f"{josa(term, '은는')} {desc}입니다."


def unspace(t):
    """'벨 트 식', '할 로 겐 용' 처럼 글자를 벌려 쓴 말을 붙임 (한 글자짜리가 셋 이상 이어질 때만)."""
    t = re.sub(r"(?<![가-힣])(?:[가-힣] ){2,}[가-힣](?![가-힣])", lambda m: m.group(0).replace(" ", ""), t or "")
    m = re.fullmatch(r"((?:[가-힣] )+[가-힣])(\s*\(.*\))?", t)         # '상 체', '몸 체 (상·하체)' 처럼 전체가 벌려 쓴 말
    return m.group(1).replace(" ", "") + (m.group(2) or "") if m else t


def subject(caption):
    """표 제목 → 무엇의 표인지: '방진마스크의 종류(등급)' → '방진마스크', '눈 보호구의 종류: 형태에 따른 종류' → '눈 보호구'."""
    if not caption:
        return ""
    t = ""
    for part in reversed(caption.split(": ")):               # 작은 제목에 이름이 있으면 그것 ('보호복의 종류: 방열복의 종류' → 방열복)
        m = re.match(r"^(.+?)의 종류", part)
        if m:
            t = m.group(1)
            break
    t = re.sub(r"\(.*?\)", "", t).strip()
    return t if 0 < len(t) <= 12 else ""


def _items(cell):
    """칸 안의 '․a ․b' 나열 → ['a', 'b']"""
    parts = [x.strip(" ,") for x in re.split(r"\s*[․·•]\s*(?=[가-힣A-Za-z0-9])", cell or "") if x.strip(" ,")]
    return parts or ([cell.strip()] if cell and cell.strip() else [])


def row_sentences(rows, caption=None):
    """표 하나(행·열, 병합 칸은 None) → [(문장, 그 줄의 원래 글자)] 목록. 문장으로 만들 수 없는 표는 빈 목록.
    원래 글자(띄어쓰기 뺀 것)는 '검색된 문단에 이 줄이 들어 있는지' 확인하는 데 씀.
    표의 모양(머리글)에 따라:
      작업명·보호구            → '○○ 작업에는 △△를 착용합니다.'
      종류·사용구분(성능·구조)  → '○○는 …것입니다.'
      등급이 가로로 놓인 표      → '방진마스크 특급은 … 에서 사용합니다.'
      종류·기호 / 착용부위 / 시험가스 → '○○의 기호는 H입니다.'
      그 밖(칸 2개 이상)         → '송기마스크: 종류 호스 마스크, 등급 폐력흡인형, 구분 안면부.' (머리글과 칸을 그대로 이어 씀)
    그리고 표마다 '○○에는 A, B, C가 있습니다.' 한 문장 (종류를 묻는 질문용)."""
    if not rows or len(rows) < 2:
        return []
    if not _head(rows[0][0]) or sum(bool(_head(c)) for c in rows[0]) < 2:   # 머리글이 없는 표(앞 쪽에서 이어진 표 등)는 건드리지 않음
        return []
    if any(c is None for c in rows[0]):                      # 머리글이 병합된 칸(None): 그 칸의 값은 앞 칸에 붙임 ('송풍기형' + '전동')
        keep = [i for i, c in enumerate(rows[0]) if c is not None]
        fixed = []
        for row in rows:
            row = list(row) + [None] * (len(rows[0]) - len(row))
            new = []
            for i, c in enumerate(row[:len(rows[0])]):
                if i in keep:
                    new.append(c)
                elif c and new:
                    new[-1] = f"{new[-1]} {c}" if new[-1] else c
            fixed.append(new)
        rows = fixed
    head = [_head(c) for c in rows[0]]
    subj = subject(caption)
    out, carry, body = [], [None] * len(head), []
    for r in rows[1:]:
        r = list(r) + [None] * (len(head) - len(r))
        merged = [c is None for c in r[:len(head)]]
        for i, c in enumerate(r[:len(head)]):               # 병합된 칸(None)은 위 칸의 값 (첫 칸, 그리고 그 밖의 표에서는 모든 칸)
            if c is None:
                r[i] = carry[i]
            else:
                carry[i] = c
        body.append((r, merged))
    col = lambda *names: next((i for i, h in enumerate(head) if h in names), None)
    named = lambda term: term if not subj or subj.replace(" ", "")[-2:] in term.replace(" ", "") else f"{subj} {term}"

    def listing(terms, what=None):
        terms = list(dict.fromkeys(t for t in terms if t and len(t) <= 24))
        if len(terms) >= 2 and (subj or what):
            name = what or f"{subj}"
            out.append((f"{josa(name, '은는')} {', '.join(terms)}{'이' if has_final(terms[-1]) else '가'} 있습니다.", _head("".join(terms))))

    w, g = col("작업명"), col("보호구")
    if w is not None and g is not None:
        return [(s, _head(r[w])) for r, m in body if not m[w] or not m[g] if (s := work_gear(r[w], r[g]))]
    # 등급이 가로로 놓인 표: ['등급', '특급', '1급', '2급'] / ['사용장소', …]
    if head[0] == "등급" and len(head) >= 3 and all(re.fullmatch(r"(특|\d)급", h or "") for h in head[1:]):
        listing([h for h in head[1:]], f"{subj} 등급" if subj else "등급")
        for r, m in body:
            label = _head(r[0])
            if m[0] and any(not x for x in m[1:]):           # 여러 칸에 걸친 덧붙임 문장
                note = clean(next(c for c, x in zip(r[1:], m[1:]) if not x))
                if note:
                    out.append((note if note.endswith(".") else note + ".", _head(note)))
                continue
            for j in range(1, len(head)):
                if m[j] or not r[j]:
                    continue
                its = [clean(x) for x in _items(r[j])]
                term = f"{subj} {head[j]}".strip()
                if label == "사용장소":
                    out.append((f"{josa(term, '은는')} {', '.join(its)}에서 사용합니다.", _head(r[j])))
                else:
                    out.append((f"{term}의 {clean(rows[0][0] and r[0])}: {', '.join(its)}.", _head(r[j])))
        return out
    t = col("종류", "분류", "종류(기호)")
    d = col("사용구분", "성능구분", "구조특징", "성능")
    grade, code = col("등급"), col("기호")
    if t is not None and d is not None:
        names = []
        for r, m in body:
            term = unspace(clean(r[t]))
            names.append(term + ("형" if head[t] == "종류(기호)" and term else ""))
            if head[t] == "종류(기호)" and term:
                term += "형"
            if grade is not None and r[grade] and clean(r[grade]) not in ("－", "-"):
                term = f"{term} {clean(r[grade])}"
            if code is not None and code != t and r[code] and term:
                term = f"{term}({clean(r[code])})"
            desc = clean(r[d] or "")
            if m[t] and not m[d] and len(desc) <= 12:          # 한 종류 아래 여러 줄 ('안전그네식' 아래 '1개 걸이용'…) → 아래에서 묶음
                continue
            s = term_desc(named(term) if head[t] == "종류(기호)" else term, desc)
            if s:
                out.append((s, _head(r[d])))
            elif code is not None and r[code] and term:        # 설명 없이 기호만 있는 줄 ('귀덮개 － EM')
                out.append((f"{josa(clean(r[t]), '의의')[:-1]}의 기호는 {clean(r[code])}입니다.", _head(r[t]) + _head(r[code])))
        # 한 종류 아래 짧은 줄이 여러 개면 한 문장으로: '안전대 안전그네식에는 1개 걸이용, U자 걸이용…이 있습니다.'
        groups = {}
        for r, m in body:
            if r[t] and r[d] and len(clean(r[d])) <= 12:
                groups.setdefault(unspace(clean(r[t])), []).append(clean(r[d]))
        for term, vals in groups.items():
            if len(vals) >= 2:
                out.append((f"{josa(named(term), '은는')} {', '.join(vals)}{'이' if has_final(vals[-1]) else '가'} 있습니다.", _head("".join(vals))))
        listing(names)
        return out
    # 종류 + 값 하나 (기호·착용부위·시험가스…)
    if len(head) == 2 and head[0] and head[1]:
        h0, h1 = unspace(clean(rows[0][0])), unspace(clean(rows[0][1]))
        terms = []
        for r, m in body:
            term, val = unspace(clean(r[0])), unspace(clean(r[1] or ""))
            if not term or not val or m[1]:
                continue
            terms.append(term)
            if h1 == "시험가스":
                val = re.sub(r"\s*\([A-Za-z ]+\)\s*[\d ]*$", "", val)   # 화학식은 글로 뽑으면 깨져서 뺌 ('(C H ) 6 12')
            if len(val) > 20:                                  # 값이 설명이면 '○○는 …입니다'
                sent = term_desc(named(term), val)
                if sent:
                    out.append((sent, _head(r[0]) + _head(r[1])))
                continue
            who = f"{term} {subj}" if h1 == "시험가스" and subj else (f"{subj} {term}" if h1 == "기호" and subj else term)
            out.append((f"{who}의 {h1.replace('시험가스', '시험 가스').replace('착용부위', '착용 부위')}{'은' if has_final(h1) else '는'} {val}입니다.", _head(r[0]) + _head(r[1])))
        listing(terms, h0 if "따른" in h0 else None)
        return out
    # 그 밖의 표: 머리글과 칸을 그대로 이어 씀 (숫자만 있는 표는 뺌)
    if len(head) >= 3 and all(head):
        names = []
        for r, m in body:
            cells = [(unspace(clean(h)), unspace(clean(c or ""))) for h, c in zip(rows[0], r)]
            cells = [(h, c) for h, c in cells if c and c not in ("－", "-")]
            if len(cells) < 2 or sum(ch.isdigit() for _, c in cells for ch in c) > sum(len(c) for _, c in cells) / 2:
                continue
            if any(len(c) > 160 for _, c in cells):
                continue
            names.append(cells[0][1])
            if len(cells) == 2:                              # '고글형 (1안식)의 특성: 1. … 2. …'
                line = f"{cells[0][1]}의 {cells[1][0]}: {cells[1][1]}"
            else:
                line = f"{subj + ': ' if subj else ''}" + ", ".join(f"{h} {c}" for h, c in cells)
            out.append((line.rstrip(".") + ".", _head("".join(c for _, c in cells[1:]))))
        listing(names)
    return out


def list_item(line):
    """조문의 '○○ 작업: 보호구' 줄 → 문장. 아니면 None."""
    m = re.match(r"^(.{6,}?(?:작업|장소|경우|업무))\s*:\s*(.{2,60})$", line.strip())
    return work_gear(m.group(1), m.group(2)) if m else None
