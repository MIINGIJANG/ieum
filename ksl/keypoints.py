"""답변의 핵심만 수어로: 긴 문서 답변에서 질문과 가장 관련 있는 내용의 핵심 단어를 골라 짧은 수어로.

- 규칙 기반 (기본, 늘 동작): 답변 문장마다 질문과 겹치는 정도 · 산업안전 핵심어 · 수어 동작이 있는 단어 비율로 점수를 매겨
  가장 관련 있는 문장을 고르고, 그 안에서 핵심 단어만 남김
    질문에 나온 말 > 산업안전 핵심어 > 수어 동작(또는 대체 표현)이 있는 말 순으로, 뜻을 바꾸는 말(없다·안하다·필요·금지 등)은 늘 남김
    수어 어순(ksl/order.py)은 그대로, 최대 MAX_UNITS 단어
- Claude (선택, ANTHROPIC_API_KEY 가 있고 KSL_KEYPOINTS=llm|auto): 답변에서 핵심 단어를 골라 달라고 하고,
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


def rule_based(pipe, question, answer_text, sentences):
    """sentences: 답변 문장 목록. 가장 관련 있는 문장의 핵심 단어."""
    best = None
    for s in sentences:
        units = pipe.sign_words.analyze(s)
        if not units:
            continue
        cov = sum(_has_sign(pipe, u["gloss"]) for u in units) / len(units)
        score = 2.0 * _overlap(question, s) + cov + (0.3 if _domain(pipe, s) else 0.0)
        if best is None or score > best[0]:
            best = (score, s, units)
    if not best:
        return None
    _, sent, units = best
    asks_time = re.search(r"언제|몇|얼마|기간|시간|주기|마다", question) is not None   # 때·양을 묻는 질문 → 숫자·시간이 답의 핵심

    def importance(u):
        w = u.get("lemma") or u["word"]
        sc = 0.0
        if _overlap(w, question) > 0.5 or (len(w) <= 2 and w in question):
            sc += 3.0                                                    # 질문에 나온 말
        if _domain(pipe, w):
            sc += 1.0                                                    # 산업안전 핵심어
        if _has_sign(pipe, u["gloss"]):
            sc += 1.0                                                    # 수어로 보여줄 수 있음
        else:
            sc -= 1.0
        if KEEP_GLOSS.match(u["gloss"]) or u.get("nms"):
            sc += 3.0                                                    # 뜻을 바꾸는 말·문법 표정이 붙은 말
        if asks_time and re.search(r"\d|개월|시간|마다|매일|매주|주기|항상|언제나", w):
            sc += 4.0                                                    # '언제·몇' 질문의 답 (6개월마다 등)
        return sc

    seen, scored = set(), []
    for i, u in enumerate(units):
        sc = importance(u) - (2.5 if u["gloss"] in seen else 0.0)       # 같은 수어가 또 나오면 (해야 함·교체 반복) 뒤로
        seen.add(u["gloss"])
        scored.append((sc, i))
    keep = {i for sc, i in sorted(scored, key=lambda x: (-x[0], x[1]))[:MAX_UNITS] if sc > 0}
    picked = [u for i, u in enumerate(units) if i in keep] or units[:MAX_UNITS]
    return {"text": sent, "units": picked, "method": "rule"}


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
    mode = os.environ.get("KSL_KEYPOINTS", "auto")                     # auto | rule | llm
    if mode in ("auto", "llm"):
        r = llm_based(pipe, question, answer_text)
        if r:
            return r
    return rule_based(pipe, question, answer_text, sentences)
