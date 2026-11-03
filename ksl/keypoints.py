"""답변의 핵심만 수어로: 긴 문서 답변에서 질문과 가장 관련 있는 내용의 핵심 단어를 골라 짧은 수어로.

- 규칙 기반 (기본): 화면 답변의 첫 문장(바로 답)을 쓰고, 다른 문장이 질문과 훨씬 더 맞으면 그 문장.
  긴 문장은 쉼표·'~하고' 등으로 나눠 질문과 맞는 부분만. 그 안의 뜻 있는 말은 빠짐없이 순서대로
  (있다·하다 같은 군말, 같은 수어 반복, 질문과 상관없는 회색 단어만 뺌. 해야 함·안 함·없다 같은 말은 늘 남김)
  수어 어순(ksl/order.py)은 그대로, 최대 MAX_UNITS 단어
- Claude (선택, ANTHROPIC_API_KEY 가 있고 KSL_KEYPOINTS=llm. 기본은 규칙): 답변에서 핵심 단어를 골라 달라고 하고,
  답변에 실제로 있는 말만 남김 (없는 내용이 생기지 않게). 결과가 모자라거나 실패하면 규칙 기반으로
반환: {"text": 핵심으로 고른 문장(화면 표시용), "units": [{gloss, word, nms, ...}], "method": "rule" | "llm"}
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


JOSA = re.compile(r"(은|는|이|가|을|를|의|에|에서|에서는|에는|으로|로|와|과|도|에게)$")


def _plain(u):
    """화면 글자: '보호구를'·'작업에서는' → '보호구'·'작업' (조사만 뗌. '6개월마다'처럼 뜻 있는 말은 그대로)"""
    w, lem = u["word"], u.get("lemma") or ""
    if lem and re.match(r"^[가-힣0-9]+$", lem) and w.startswith(lem) and JOSA.fullmatch(w[len(lem):] or "은"):
        return lem
    return w


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
    picked = [dict(u, word=_plain(u)) for u in picked]
    for k in range(len(picked) - 1):                 # '착용해야 / 해야 함' → '착용 / 해야 함' (화면 글자만)
        if picked[k + 1]["gloss"].startswith("필요") and re.search(r"야$", picked[k]["word"]):
            picked[k] = dict(picked[k], word=re.sub(r"(해|어|아|여)?야$", "", picked[k]["word"]) or picked[k]["word"])
    return {"text": text, "units": picked, "method": "rule"}


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


def extract(pipe, question, answer_text, sentences):
    # 기본은 규칙 (결과가 늘 같고 시험 가능. Claude 답변 모드면 답변 글이 이미 짧고 정확해서 규칙으로 충분)
    mode = os.environ.get("KSL_KEYPOINTS", "rule")                     # rule | llm
    if mode == "llm":
        r = llm_based(pipe, question, answer_text)
        if r:
            return r
    return rule_based(pipe, question, answer_text, sentences)
