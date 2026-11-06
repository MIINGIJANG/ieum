"""음성 입력처럼 바뀐 글: 군말 빼기, 끝맺음 바꿔 확정 안내 문장 찾기, 띄어쓰기 붙여 다시 검색."""
from ksl.pipeline import clean_spoken, ending_variants


def test_clean_spoken():
    assert clean_spoken("어 위험한 음 곳에서는 작업을 중지하세요") == "위험한 곳에서는 작업을 중지하세요"
    assert clean_spoken("음 어떻게 해요") == "어떻게 해요"            # '어떻게' 는 그대로
    assert clean_spoken("어") == "어"


def test_ending_variants():
    assert "위험한 곳에서는 작업을 중지하세요" in ending_variants("위험한 곳에서는 작업을 중지해야 돼")
    assert "기계를 멈추세요" in ending_variants("기계를 멈춰야 돼")
    assert "기계 사용을 금지합니다" in ending_variants("기계 사용을 금지해요")


def test_voice_like_queries(pipe):
    assert pipe.query("정전기 안전화는 어떤 역할을 해요")["mode"] == "ask"   # 문서는 '정전기안전화' 로 붙여 씀


def test_substitute_and_short_sign(pipe):
    """동작 없는 단어는 대체 표현으로(기록), 답변 수어는 화면 답변의 핵심 문장에서 (준비된 질문도)."""
    r = pipe.sign_sentence("안전모를 쓰세요.")
    assert r["gloss"][:2] == ["머리0", "보호1"] and r["sub_of"][:2] == ["안전모", "안전모"]
    assert any(x["gloss"] == "안전모" for x in pipe.missing.top(subs=True))
    a = pipe.query("보호구에 이상이 있는지 어떻게 확인해요?")
    sa = a["sentences"][0]
    assert a["answer_mode"] == "demo" and sa["method"] == "keypoints_sign"          # 화면 답변을 사람이 줄여 둔 문장
    assert {"보호1", "이상하다1"} <= set(sa["gloss"])
    b = pipe.query("정화통은 언제 교체해요?")
    sg = b["sentences"][0]
    assert len(b["sentences"]) == 1 and sg["method"].startswith("keypoints_") and sg["korean"] in b["answer_text"]
    assert len(sg["gloss"]) <= 7 and any("6개월" in (w or "") for w in sg["words"])   # 핵심 단어만, '언제' 질문은 기간 포함
    assert {"정화통", "바꾸다1"} <= set(sg["gloss"])


def test_blocks_always_full_sentence_for_clean_answer(pipe):
    """수어용 문장이 없을 때(Claude 실패 등): 끝맺음 있는 답변이면 단어를 골라내지 않고 그 문장을 통째로 → 블록만 읽어도 문장."""
    from ksl import keypoints
    from ksl.pipeline import split_sentences
    a = "높이 또는 깊이 2미터 이상의 추락할 위험이 있는 장소에서 하는 작업은 안전대를 착용해야 합니다."
    r = keypoints.extract(pipe, "2미터 넘는 높은 곳에서는 어떤 보호구를 착용해요?", a, split_sentences(a))
    ws = [u["word"] for u in r["units"]]
    assert r["method"] == "sentence" and ws[-2:] == ["착용해야", "합니다"] and not keypoints.check_blocks(r["units"], r["text"])
    a2 = "정전기안전화는 물체의 낙하, 충격 또는 날카로운 물체에 의한 찔림 위험으로부터 발을 보호합니다."
    ws2 = [u["word"] for u in keypoints.extract(pipe, "정전기안전화는 어떤 역할을 해요?", a2, [a2])["units"]]
    assert ws2[:2] == ["정전기", "안전화는"] and "낙하 그리고" in ws2 and ws2[-1] == "보호합니다"   # 붙은 말·쉼표 처리


def test_keypoints_short_answer_keeps_all_meaning(pipe):
    """짧은 답변(Claude 답변 모드)은 뜻 있는 말을 빠짐없이: '정화통은 6개월마다 교체해야 합니다' → 6개월 정화통 교체 해야 함."""
    from ksl import keypoints
    a = "정화통은 6개월마다 교체해야 합니다."
    r = keypoints.rule_based(pipe, "정화통은 언제 교체해요?", a, [a])
    assert [u["gloss"] for u in r["units"]] == ["6개월", "정화통", "바꾸다1", "필요1"]
    assert [u["word"] for u in r["units"]] == ["6개월마다", "정화통은", "교체해야", "합니다"]   # 블록이 문장 어절 그대로
    a = "보호구는 늘 사용할 수 있도록 관리하고 깨끗하게 유지해야 합니다. 사용하지 않을 때는 건조한 곳에 보관합니다."
    g = [u["gloss"] for u in keypoints.rule_based(pipe, "보호구는 어떻게 관리해요?", a, [a.split(". ")[0] + ".", a.split(". ")[1]])["units"]]
    assert "보호1" in g and "깨끗하다1" in g and "필요1" in g and not any(x.startswith("가능") for x in g)   # 첫 문장, '할 수 있도록'은 뺌


def test_keypoints_llm_only_uses_answer_words(pipe, monkeypatch):
    """Claude 로 핵심 고르기: 답변에 없는 말은 버림, 결과가 모자라면 규칙 기반으로 (실제 API 대신 가짜 응답)."""
    import json as _json
    import types
    import anthropic
    from ksl import keypoints

    answer = "정화통은 6개월마다 교체하고, 보관 환경은 온도 5~30℃를 유지해야 한다."
    reply = {"key_sentence": "정화통은 6개월마다 교체하고", "keywords": ["정화통", "6개월", "교체", "우주선"]}

    class Fake:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema" and kw["fallbacks"] == "default"
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=_json.dumps(reply, ensure_ascii=False))])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("KSL_KEYPOINTS", "llm")                              # 기본은 규칙, 켰을 때만 Claude
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    r = keypoints.extract(pipe, "정화통은 언제 교체해요?", answer, [answer])
    assert r["method"] == "llm" and "우주선" not in [u["word"] for u in r["units"]]
    reply["keywords"] = ["우주선", "로켓"]                                 # 답변에 없는 말뿐 → Claude 결과는 버리고 다른 방식으로
    assert keypoints.extract(pipe, "정화통은 언제 교체해요?", answer, [answer])["method"] in ("sentence", "rule")


def test_sign_hint_from_claude_and_answers(pipe, monkeypatch):
    """수어로 보여줄 말: Claude 답변의 '수어:' 줄(답변에 있는 말만) 또는 준비된 답변의 'sign' 문장에서 뜻 있는 말을 모두."""
    import types
    from ksl import keypoints, rag
    answer = ("제조자의 지침을 항상 따라야 하며 일반적으로 비마모성 중성 세정제와 온수, 부드러운 천으로 세척한 후 "
              "완전히 헹구고 건조시켜야 합니다.")
    r = keypoints.extract(pipe, "보호구 세척은 어떻게 해요?", answer, [answer],
                          hint="중성 세정제와 온수로 세척한 후 헹구고 건조시켜야 합니다.")
    words = [u["word"] for u in r["units"]]
    assert r["method"] == "sign" and words == ["중성", "세정제와", "온수로", "세척한", "후", "헹구고", "건조시켜야", "합니다"]
    bad = keypoints.extract(pipe, "보호구 세척은 어떻게 해요?", answer, [answer], hint="표백제로 소독해야 합니다.")
    assert bad["method"] == "rule"                                        # 답변에 없는 말(표백제·소독) → 쓰지 않음
    assert keypoints.from_hint(pipe, "세척하면 안 됩니다.", answer) is None                 # 답변에 없는 부정 → 뜻이 뒤집혀서 안 씀
    ng = keypoints.from_hint(pipe, "손상된 보호구는 사용하면 안 됩니다.", "손상된 보호구는 사용하면 안 됩니다.")
    assert [u["word"] for u in ng["units"]][-1] == "안 됩니다"
    ws = [u["word"] for u in keypoints.from_hint(pipe, "보호구는 수시로 점검해서 이상이 있으면 바꿔야 합니다.",
                                                 "보호구는 수시로 점검해서 이상이 있으면 바꿔야 합니다.")["units"]]
    assert "있으면" in ws and ws[-2:] == ["바꿔야", "합니다"]                       # 수어가 있는 '있다'는 빼지 않음
    cw = [u["word"] for u in keypoints.from_hint(pipe, "중성 세정제와 온수, 부드러운 천으로 세척해야 합니다.", answer)["units"]]
    assert "온수 그리고" in cw and "부드러운" in cw                                  # 쉼표 → '그리고' (문장이 이어지게)
    assert keypoints.from_hint(pipe, "보호구를 씻고 건조시켜야 합니다.", answer)["method"] == "sign"   # 수어 단어 목록의 같은 뜻 말(씻다)은 됨

    # 2단계: ① Claude 가 근거로 답변 → ② 그 답변으로 수어용 짧은 문장 (가짜 응답)
    calls = []

    def create(**kw):
        calls.append(kw)
        out = {keypoints.SIGN_SYSTEM: "중성 세정제와 온수로 세척하고 헹군 후 건조해야 합니다.",
               keypoints.REVIEW_SYSTEM: "OK"}.get(kw.get("system"), answer)
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=out)])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(rag, "_CLIENT", types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    rag._LLM_CACHE.clear()
    keypoints._SIGN_CACHE.clear()
    q = pipe.query("보호구 세척은 어떻게 해요?")
    assert q["answer_mode"] == "llm" and q["answer_text"] == answer and len(calls) == 3   # 답변 → 수어 문장 → 검수
    assert answer in calls[1]["messages"][0]["content"] and "수어 단어" in calls[1]["messages"][0]["content"]
    g = q["sentences"][0]
    assert g["method"] == "keypoints_sign" and g["korean"].startswith("중성 세정제")
    assert [w for w in g["words"] if w][-1] == "합니다"
    rag._LLM_CACHE.clear()
    keypoints._SIGN_CACHE.clear()

    # 검수: 블록 문장이 어색하면('위험이 있고') 고친 문장으로
    ans2 = "높이 2미터 이상인 곳에서 추락할 위험이 있으면 안전대를 착용해야 합니다."
    bad = keypoints.from_hint(pipe, "높이 2미터 이상인 곳에서 추락할 위험이 있고 안전대를 착용해야 합니다.", ans2)
    assert any("조건" in p for p in keypoints.check_blocks(bad["units"], bad["text"]))
    monkeypatch.setattr(rag, "_CLIENT", types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: types.SimpleNamespace(
        stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text="추락할 위험이 있으면 안전대를 착용해야 합니다.")]))))
    fixed = keypoints.llm_review(pipe, "높은 곳에서는?", ans2, bad)
    assert fixed.get("reviewed") and "있으면" in [u["word"] for u in fixed["units"]]
    assert not keypoints.check_blocks(fixed["units"], fixed["text"])
    keypoints._SIGN_CACHE.clear()

    # 준비된 질문: 사람이 줄여 둔 문장
    d = pipe.query("보호구는 어떻게 관리해요?")
    assert d["answer_mode"] == "demo" and d["sentences"][0]["method"] == "keypoints_sign"
    assert {"보호1", "깨끗하다1", "필요1"} <= set(d["sentences"][0]["gloss"])
