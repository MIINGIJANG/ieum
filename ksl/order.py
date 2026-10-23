"""한국어 답변에서 뽑은 수어 단어 → 한국수어 어순 + 문법 비수지 표현(NMS).

한국수어는 한국어처럼 '주어-목적어-동사' 순서라 단어 순서는 대부분 그대로 두고,
한국어에서는 어미·보조용언으로 붙는 문법 요소를 수어 단어로 꺼내 동사 뒤에 둔다.
(조원 확정 문장·샘플 글로스: '착용하지 않으면' → 착용 안하다, '반드시 착용해야 한다' → 착용 꼭 필요,
 '다칠 수 있습니다' → 다치다 가능, '이상이 없는지 확인' → 이상하다 없다 확인)

절(clause) 마다
  1. 시간·빈도 표현(6개월, 4시간, 정기적, 항상 …)은 절 맨 앞      — 수어는 시간을 먼저 세움
  2. 나머지 단어는 한국어 순서 그대로
  3. 강조 부사(반드시·꼭·절대)는 동사 뒤                          — 착용 꼭 필요
  4. 부정·금지·필요·가능은 동사 뒤                                — 착용 안하다 / 벗다 금지 / 교체 필요 / 다치다 가능
     ('-지 않으면 안 된다' 같은 이중 부정은 '필요')

비수지 표현 (아바타 표정·고개, web/avatar.js 가 단어 표정 표보다 먼저 씀)
  cond     조건절('-면', '-ㄹ 때')    눈썹 올림 + 고개 살짝 뒤로      (조건·화제 표지)
  neg      부정                       고개 젓기 + 찡그림               (동작 없는 '안하다' 대신 동사 위에서도 고개를 저음)
  prohibit 금지                       고개 젓기 + 단호한 얼굴
  must     필요('-해야')              단호하게 끄덕
  emph     강조(꼭·반드시·절대)        찡그린 얼굴 + 끄덕
  can      가능('-ㄹ 수 있다')         작게 끄덕
"""
import re

# 문법 요소 → 수어 단어 (동작 파일이 있으면 아바타가 움직이고, 없으면 회색 단어 + 표정만)
MARK_GLOSS = {"neg": "안하다", "prohibit": "금지", "must": "필요1", "can": "가능1", "cannot": "못하다"}
MARK_WORD = {"neg": "안 함", "prohibit": "금지", "must": "해야 함", "can": "할 수 있음", "cannot": "못 함"}

EMPH = {"반드시", "꼭", "절대", "절대로"}
TIME_WORDS = {"연", "항상", "늘", "자주", "수시로", "정기", "정기적", "주기", "주기적", "매일", "매년", "매월", "평소"}
_TIME = re.compile(r"^\d+(?:\.\d+)?(?:개월|시간|년|주|일|분|초|회)$")
TIME_TAIL = {"이상", "이하", "이내", "미만", "초과", "동안", "마다", "전", "후"}
COND_NOUNS = {"때", "경우"}

NMS = {
    "cond":     {"face": {"surprised": 0.45}, "head": {"type": "up", "deg": 5}},
    "neg":      {"face": {"sad": 0.3, "angry": 0.25}, "head": {"type": "shake", "n": 2, "deg": 14}},
    "prohibit": {"face": {"angry": 0.6}, "head": {"type": "shake", "n": 2, "deg": 12}},
    "must":     {"face": {"angry": 0.35}, "head": {"type": "nod", "n": 1, "deg": 10}},
    "emph":     {"face": {"angry": 0.45}, "head": {"type": "nod", "n": 1, "deg": 8}},
    "can":      {"face": {"relaxed": 0.3}, "head": {"type": "nod", "n": 1, "deg": 5}},
}
_HEAD_RANK = ["neg", "prohibit", "must", "emph", "can", "cond"]     # 고개 움직임은 앞쪽 역할이 우선


def nms_for(roles):
    """역할 여러 개 → 표정은 큰 값끼리 합치고, 고개 움직임은 가장 중요한 역할 하나."""
    if not roles:
        return None
    face = {}
    for r in roles:
        for k, v in NMS[r]["face"].items():
            face[k] = max(face.get(k, 0), v)
    top = min(roles, key=_HEAD_RANK.index)
    return {"roles": sorted(set(roles), key=_HEAD_RANK.index), "face": face, "head": dict(NMS[top]["head"])}


def _clauses(toks):
    """토큰 → [(시작, 끝(포함), 조건절?)]. 연결어미(-면, -고, -어서 …)와 관형형('-ㄹ 때', '-ㄴ 근로자') 뒤에서 나눔.
    '-지 않', '-어야 하', '-지 말' 처럼 뒤에 보조용언이 오면 나누지 않고, '-면/-어서는 안 되다' 도 한 절."""
    out, s, n = [], 0, len(toks)
    i = 0
    while i < n:
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < n else None
        end, cond = None, False
        if t.tag == "EC" and not (nxt and nxt.tag.startswith("VX")):
            j = i + 1
            while j < n and toks[j].tag == "JX":                  # '-어서는'
                j += 1
            if j + 1 < n and toks[j].form == "안" and toks[j].tag == "MAG" and toks[j + 1].form == "되":
                i = j + 2                                          # '-면 안 되다' / '-어서는 안 되다' → 금지, 이 절에 포함
                continue
            end, cond = i, t.form in ("면", "으면")
        elif t.tag == "ETM":
            if nxt and nxt.form in COND_NOUNS:                    # '작업할 때' → 조건절 '작업 때'
                end, cond = i + 1, True
                i += 1
            elif nxt and nxt.tag.startswith("NN") and nxt.form not in ("수", "것", "데", "중"):
                end = i                                            # '착용하지 않은 근로자' → 관형절
        if end is not None:
            out.append((s, end, cond))
            s = end + 1
        i += 1
    if s < n:
        out.append((s, n - 1, False))
    return out


def _marks(toks, a, b):
    """절 안의 문법 요소 → ['neg', 'must', …] (나타난 순서)."""
    m = []
    for i in range(a, b + 1):
        t, nxt = toks[i], toks[i + 1] if i + 1 < len(toks) else None
        if t.tag == "VX" and t.form in ("않", "못하"):
            m.append("neg")
        elif t.tag == "VX" and t.form == "말":
            m.append("prohibit")
        elif t.tag == "MAG" and t.form in ("안", "못"):
            if nxt and nxt.form == "되" and i > 0 and toks[i - 1].tag in ("EC", "JX"):
                m.append("prohibit")                               # '-면 안 된다'
            elif nxt and nxt.tag.startswith(("VV", "VA", "NNG")):
                m.append("neg")                                    # '안 쓰면' → 쓰다 안하다
        elif t.tag == "EC" and t.form in ("어야", "아야", "여야") and nxt and nxt.form in ("하", "되"):
            m.append("must")
        elif t.form == "수" and t.tag == "NNB" and nxt and nxt.form in ("있", "없"):
            m.append("can" if nxt.form == "있" else "cannot")
    if "neg" in m and "prohibit" in m:                             # '-지 않으면 안 된다' = 해야 한다
        m = [x for x in m if x not in ("neg", "prohibit")] + ["must"]
    return list(dict.fromkeys(m))


def arrange(units, toks):
    """units: 한국어 순서의 [{gloss, word, lemma, ti(토큰 번호)}] → 수어 순서 + 'nms'.
    문법 요소 단어(안하다·필요 등)는 새로 넣고, 동작이 없어도 동사 위의 표정·고개로 전해지게 동사에도 같은 역할을 붙임."""
    out = []
    for a, b, cond in _clauses(toks):
        cu = [dict(u, roles=[]) for u in units if a <= u["ti"] <= b]
        marks = _marks(toks, a, b)
        if not cu and not marks:
            continue
        time = []
        for n, u in enumerate(cu):
            if _TIME.match(u["lemma"]) or u["lemma"] in TIME_WORDS:
                time.append(u)
                for v in cu[n + 1:]:                               # '4시간 이상', '6개월 동안' 은 같이 움직임
                    if v["lemma"] not in TIME_TAIL:
                        break
                    time.append(v)
        emph = [u for u in cu if u["lemma"] in EMPH]
        rest = [u for u in cu if u not in time and u not in emph]
        last = [rest.pop()] if rest and rest[-1]["lemma"] in COND_NOUNS else []   # '사용 안하다 때': 부정은 '때' 앞 동사 뒤
        for u in emph:
            u["roles"].append("emph")
        tail = []
        for m in marks:
            role = "neg" if m == "cannot" else m
            if rest:
                rest[-1]["roles"].append(role)                     # 동사(절의 마지막 단어)에도 같은 표정
            tail.append({"gloss": MARK_GLOSS[m], "word": MARK_WORD[m], "lemma": m, "ti": b, "roles": [role]})
        clause = time + rest + emph + tail + last
        if cond:
            for u in clause:
                u["roles"].append("cond")
        out += clause
    for u in out:
        u["nms"] = nms_for(u.pop("roles"))
    return out
