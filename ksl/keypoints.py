"""답변을 수어로: 화면에는 자세한 답변, 수어(단어 블록)는 그 답변을 짧고 완성된 한 문장으로 줄인 것.

순서 (extract)
1. 수어용 문장(hint)이 있으면 그 문장의 뜻 있는 말을 모두 순서대로 → 블록만 읽어도 완성된 문장
   - 준비된 질문: 사람이 줄여 둔 문장 (data/kosha/answers.json 의 sign)
   - Claude 답변 모드 (2단계): ① 문서 근거로 답변(ksl/rag.py) → ② 그 답변을 Claude 가 수어 동작이 있는 말 위주의
     짧은 한 문장으로 (llm_sign_sentence). 답변에도 수어 단어 목록에도 없는 말, 답변에 없는 부정·금지가 있으면 쓰지 않음
2. 없으면 규칙 방식 (rule_based): 답변의 첫 문장(또는 질문과 맞는 부분)의 뜻 있는 말
반환: {"text": 수어로 보여주는 문장, "units": [{gloss, word, nms, ...}], "method": "sign" | "rule" | "llm"}
"""
import json
import os
import re

MAX_UNITS = 7
# 뜻을 바꾸는 말 (빼면 반대 뜻이 됨): 부정·금지·의무·가능
KEEP_GLOSS = re.compile(r"^(없다|안하다|아니다|못하다|금지|필요|꼭|가능|불가능|조심|위험)")


def _bigrams(t):
    s = re.sub(r"[\s.,!?·()]", "", t)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a, b):
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / max(1, len(A))


def _has_sign(pipe, g):
    return bool(pipe.motion and pipe.motion.get(g)) or (
        g in pipe.substitutes and all(pipe.motion and pipe.motion.get(x) for x in pipe.substitutes[g]))


def _domain(pipe, word):
    w = re.sub(r"\s", "", word)
    return any(t in w for t in pipe.domain_terms)


FILLER = {"있다", "하다", "되다", "것", "수", "위하다", "등", "및", "경우", "이때", "대하다", "의하다", "통하다", "때문"}
LONG = 45                                         # 이보다 긴 문장은 쉼표·'~하고/~하며'로 나눠 질문과 맞는 부분만


def _clauses(sentence):
    if len(sentence) <= LONG:
        return [sentence]
    parts = re.split(r"(?<=[고며])\s+|(?<=므로)\s+|,\s*|:\s*", sentence)
    return [x.strip() for x in parts if len(x.strip()) >= 4] or [sentence]


def _eff(pipe, g):
    """화면·동작에 실제로 쓰일 첫 수어 (대체 표현이면 그 첫 수어) → 같은 수어가 두 번 나오지 않게."""
    if pipe.motion and pipe.motion.get(g):
        return g
    sub = pipe.substitutes.get(g)
    return sub[0] if sub else g


def rule_based(pipe, question, answer_text, sentences):
    """답변의 핵심 문장(또는 긴 문장의 핵심 부분)을 고르고, 그 안의 뜻 있는 말을 순서대로 모두 수어로.
    - 문장 고르기: 질문과 겹치는 정도가 가장 큰 문장/부분 (같으면 앞쪽 = 답변의 첫 문장)
    - 단어: 있다·하다·것 같은 군말, 같은 수어 반복, '~할 수 있도록'의 '가능'은 뺌
            수어가 없는 말(회색)은 질문에 나온 말·숫자·기간·산업안전 핵심어만 남김 (그 밖의 회색은 뺌)
            뜻을 바꾸는 말(해야 함·안 함·없다·금지)은 늘 남김. 최대 MAX_UNITS (넘으면 덜 중요한 것부터 뺌)"""
    asks_time = re.search(r"언제|몇|얼마|기간|시간|주기|마다", question) is not None   # 때·양을 묻는 질문 → 숫자가 든 부분

    def score(t):
        return _overlap(question, t) + (0.3 if asks_time and re.search(r"\d", t) else 0.0)

    sents = [s for s in sentences if pipe.sign_words.analyze(s)]
    if not sents:
        return None
    # 문장: 답변의 첫 문장(보통 바로 답)을 쓰되, 다른 문장이 질문과 훨씬 더 맞으면 그 문장
    best = max(sents, key=score)
    sent = best if score(best) > score(sents[0]) + 0.25 else sents[0]
    # 긴 문장이면 질문과 가장 맞는 부분 (같으면 앞쪽)
    parts = [c for c in _clauses(sent) if pipe.sign_words.analyze(c)] or [sent]
    text = max(parts, key=lambda c: (round(score(c), 2), -parts.index(c)))
    units = pipe.sign_words.analyze(text)
    purpose = re.search(r"수\s*있도록", text) is not None

    def keep(u):
        g, w = u["gloss"], u.get("lemma") or u["word"]
        if purpose and g.startswith("가능"):          # '사용할 수 있도록'의 '수 있음'은 목적이라 뺌
            return False
        if KEEP_GLOSS.match(g):
            return True
        if w in FILLER or g in FILLER:
            return False
        if _has_sign(pipe, g):
            return True
        return (w in question or _overlap(w, question) > 0.5 or re.search(r"\d", w) is not None or _domain(pipe, w))

    picked, seen = [], set()
    for u in units:
        e = _eff(pipe, u["gloss"])
        if e in seen or not keep(u):
            continue
        seen.add(e)
        picked.append(u)
    while len(picked) > MAX_UNITS:                   # 너무 길면: 질문과 상관없고 뜻을 바꾸지 않는 말부터 뒤에서 뺌
        drop = next((k for k in range(len(picked) - 1, -1, -1)
                     if not KEEP_GLOSS.match(picked[k]["gloss"]) and (picked[k].get("lemma") or "") not in question), len(picked) - 1)
        picked.pop(drop)
    picked = picked or units[:MAX_UNITS]
    return {"text": text, "units": _sentence_words(picked, text), "method": "rule"}


SYSTEM = ("너는 산업안전 문서의 답변을 청각장애인에게 수어로 짧게 전하기 위해 핵심 단어를 고른다. "
          "답변에 실제로 있는 말만 고르고, 답변에 없는 내용은 절대 더하지 않는다. "
          "부정·금지·의무(없다, 안 된다, 반드시, 해야 한다)처럼 뜻을 바꾸는 말은 빼지 않는다.")


def llm_based(pipe, question, answer_text):
    """Claude 로 핵심 단어 고르기. 키가 없거나 실패하면 None."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=20.0, max_retries=1)
        vocab = sorted({re.sub(r"[_\-]?\d+#?$", "", g) for g in (pipe.motion.index if pipe.motion else {})} | set(pipe.substitutes))
        r = client.beta.messages.create(
            model=os.environ.get("KSL_KEYPOINT_MODEL", "claude-opus-5-5"),
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                                         # 거절되면 서버가 다른 모델로 다시 (켜 둠)
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": {
                    "type": "object",
                    "properties": {
                        "key_sentence": {"type": "string", "description": "답변의 핵심을 담은 짧은 한 문장 (답변의 표현 그대로)"},
                        "keywords": {"type": "array", "items": {"type": "string"},
                                     "description": "핵심 단어 3~7개, 답변에 있는 말 그대로, 한국수어 어순(주제·장소 먼저, 부정·의무는 뒤)"},
                    },
                    "required": ["key_sentence", "keywords"], "additionalProperties": False}},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       f"질문: {question}\n\n답변: {answer_text}\n\n"
                       f"수어 동작이 있는 단어(가능하면 이 단어들을 고르기): {', '.join(vocab)}"}],
        )
        if r.stop_reason == "refusal":
            return None
        data = json.loads(next(b.text for b in r.content if b.type == "text"))
    except Exception:
        return None
    flat = re.sub(r"\s", "", answer_text)
    units = []
    for kw in data.get("keywords", [])[:MAX_UNITS]:
        if re.sub(r"\s", "", kw)[:2] not in flat:                        # 답변에 없는 말은 버림 (지어내기 방지)
            continue
        units += pipe.sign_words.analyze(kw)[:2]
    key = data.get("key_sentence", "")
    if len(units) < 2 or _overlap(key, answer_text) < 0.6:
        return None
    return {"text": key, "units": units[:MAX_UNITS], "method": "llm"}


HINT_MAX = 10
MARKS = ("must", "can", "cannot", "neg", "prohibit")
MARK_EJ = re.compile(r"합니다|해야|됩니다|돼요|안\s|않|없|못|금지|수\s?있|마세요|마십시오|하세요|한다|된다|해요")


def _sentence_words(units, text):
    """블록 글자를 문장에 쓰인 어절 그대로: '건조시켜야 / 합니다' (블록만 순서대로 읽어도 문장이 되게).
    뜻을 바꾸는 말(해야 함·안 함 등)은 문장에서 다른 블록이 쓰지 않은 그 표현 어절('합니다', '안 됩니다')로."""
    ej = [w.strip(".,!?·") for w in text.split()]
    used = set()
    for u in units:
        if (u.get("lemma") or "") in MARKS:
            continue
        w = re.sub(r"\s", "", u["word"])
        for i, e in enumerate(ej):
            if i not in used and w and (e.startswith(w) or w in e):
                used.add(i)
                break
    groups, cur = [], []                                   # 다른 블록이 쓰지 않은 어절 묶음 (문장 순서)
    for i, e in enumerate(ej):
        if i in used or not e:
            if cur:
                groups.append(cur)
            cur = []
        else:
            cur.append(e)
    if cur:
        groups.append(cur)
    picks = []                                             # 묶음 안에서 그 표현 어절만 ('안 됩니다', '수 있습니다', '합니다')
    for g in groups:
        txt = " ".join(g) + " "
        m = MARK_EJ.search(txt)
        if not m:
            continue
        pos, k0 = 0, 0
        for k, e in enumerate(g):                         # 표현이 시작하는 어절
            if pos + len(e) >= m.start():
                k0 = k
                break
            pos += len(e) + 1
        end = m.end()
        pos, k1 = 0, k0
        for k, e in enumerate(g):                         # 표현이 끝나는 어절 (최대 2어절)
            if pos < end:
                k1 = k
            pos += len(e) + 1
        if len(g[k0]) <= 1 and k0 + 1 < len(g):          # '안 / 됩니다', '못 / 합니다' 처럼 한 글자면 다음 어절까지
            k1 = k0 + 1
        picks.append(" ".join(g[k0:min(k1, k0 + 1) + 1]))
    groups = picks
    out = []
    for u in units:
        if (u.get("lemma") or "") in MARKS and groups:
            u = dict(u, word=groups.pop(0))
        out.append(u)
    return out


def from_hint(pipe, hint, answer_text):
    """수어용 짧은 문장(준비된 답변의 'sign', Claude 답변의 '수어:' 줄)의 뜻 있는 말을 모두 순서대로.
    문장에 답변에 없는 말이 있으면 쓰지 않음 (지어낸 내용 방지). 수어 동작이 없는 말도 회색으로 남김 (뜻이 끊기지 않게)."""
    if not hint:
        return None
    units = pipe.sign_words.analyze(hint)
    flat = re.sub(r"\s", "", answer_text)
    neg_ok = re.search(r"않|안\s|안된|금지|없|못|말아|마십|마세", answer_text) is not None
    for u in units:
        lem = u.get("lemma") or u["word"]
        if lem in ("neg", "prohibit", "cannot") or u["gloss"] in ("안하다", "금지", "못하다", "없다1"):
            if not neg_ok:
                return None                                             # 답변에 없는 부정·금지 → 뜻이 뒤집힘
            continue
        if not re.match(r"^[가-힣0-9]", lem) or lem in ("must", "can"):
            continue
        in_answer = re.sub(r"다$", "", lem)[:2] in flat or re.sub(r"\s", "", u["word"])[:2] in flat
        if not in_answer and not _has_sign(pipe, u["gloss"]):
            return None                                                 # 답변에도 수어 단어 목록에도 없는 말 → 지어낸 말로 보고 쓰지 않음
    picked, prev = [], None
    for u in units:
        e, w = _eff(pipe, u["gloss"]), u.get("lemma") or u["word"]
        filler = (w in FILLER or u["gloss"] in FILLER) and not _has_sign(pipe, u["gloss"])   # '있으면'처럼 수어가 있는 말은 남김 (문장이 끊기지 않게)
        if e == prev or filler:                                         # 바로 앞과 같은 수어만 뺌 (세정제·세척처럼 떨어져 있으면 둘 다)
            continue
        prev = e
        picked.append(u)
    if len(picked) > HINT_MAX:                                         # 너무 긴 문장: 블록을 빼면 문장이 끊기므로 쓰지 않음
        return None
    picked = [dict(u, word=u["word"]) for u in picked]
    return {"text": hint, "units": _sentence_words(picked, hint), "method": "sign"} if len(picked) >= 2 else None


SIGN_SYSTEM = ("너는 산업안전 안내를 청각장애인에게 한국수어로 전하기 위해, 답변을 수어로 옮길 짧은 한국어 문장 하나로 바꾼다. "
               "답변에 없는 내용은 절대 더하지 않는다. 부정·금지·의무(안 된다, 하지 마라, 해야 한다)는 반드시 살린다.")
_SIGN_CACHE = {}


def sign_vocab(pipe):
    """수어 동작이 있는 말 (동작사전 글로스 + 단어표에서 동작 있는 글로스로 연결되는 말 + 대체 표현이 있는 말)."""
    if getattr(pipe, "_sign_vocab", None) is None:
        words = {re.sub(r"[_\-]?\d+#?$", "", g) for g in (pipe.motion.index if pipe.motion else {})}
        words |= {w for w, _, g in pipe.sign_words.rules if _has_sign(pipe, g)}
        words |= set(pipe.substitutes)
        pipe._sign_vocab = sorted(w for w in words if re.match(r"^[가-힣]", w))
    return pipe._sign_vocab


def llm_sign_sentence(pipe, question, answer_text):
    """2단계: Claude 가 만든 답변 → 수어로 보여줄 짧고 완성된 한 문장 (가능하면 수어 동작이 있는 말로).
    블록(수어 단어)만 순서대로 읽어도 뜻이 통하게. 실패하면 None (규칙 방식으로)."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    key = (question.strip(), answer_text.strip())
    if key in _SIGN_CACHE:
        return _SIGN_CACHE[key]
    try:
        from .rag import client
        prompt = (f"[질문]\n{question}\n\n[답변]\n{answer_text}\n\n"
                  "위 답변을 수어로 옮길 한국어 문장 하나로 바꿔 줘.\n"
                  "- 답변의 핵심(무엇을, 언제·어디서, 어떻게, 해야 하는지·하면 안 되는지)을 빠짐없이 담은 완성된 평서문 한 문장\n"
                  "- 질문에 대한 가장 중요한 한 가지만. 내용어(명사·동사·형용사) 3~6개. 조사·어미는 자연스럽게\n"
                  "- 되도록 아래 '수어 단어' 목록의 말을 써 (뜻이 같으면 목록의 말로 바꿔도 됨). 목록에 없는 말은 꼭 필요한 것만\n"
                  "- 답변에 없는 내용은 더하지 말 것. 문장만 출력 (따옴표·설명 없이)\n\n"
                  f"[수어 단어]\n{', '.join(sign_vocab(pipe))}")
        r = client().messages.create(model=os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"), max_tokens=150,
                                     system=SIGN_SYSTEM, messages=[{"role": "user", "content": prompt}])
        if r.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in r.content if b.type == "text").strip().split("\n")[0].strip(" \"'“”")
    except Exception as e:
        print("[이음] 수어 문장 만들기 실패:", e)
        return None
    if len(_SIGN_CACHE) > 500:
        _SIGN_CACHE.clear()
    _SIGN_CACHE[key] = text or None
    return text or None


def extract(pipe, question, answer_text, sentences, hint=None):
    # 기본은 규칙 (결과가 늘 같고 시험 가능. Claude 답변 모드면 답변 글이 이미 짧고 정확해서 규칙으로 충분)
    r = from_hint(pipe, hint, answer_text)                             # 수어용 짧은 문장이 있으면 그것 (사람·Claude 가 줄인 문장)
    if r:
        return r
    mode = os.environ.get("KSL_KEYPOINTS", "rule")                     # rule | llm
    if mode == "llm":
        r = llm_based(pipe, question, answer_text)
        if r:
            return r
    return rule_based(pipe, question, answer_text, sentences)
