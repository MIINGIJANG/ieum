"""Claude 답변 모드: SDK 호출 실패·거절이면 추출식으로 대체 (실제 API 는 부르지 않음)."""
import types

import ksl.rag as R


class FakeMessages:
    def __init__(self, stop, text):
        self.stop, self.text, self.calls = stop, text, 0

    def create(self, **kw):
        self.calls += 1
        assert kw["max_tokens"] == 300 and kw["messages"][0]["role"] == "user"
        return types.SimpleNamespace(stop_reason=self.stop, content=[types.SimpleNamespace(type="text", text=self.text)])


def run(monkeypatch, rag, stop, text):
    fake = FakeMessages(stop, text)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(R, "_CLIENT", types.SimpleNamespace(messages=fake))
    R._LLM_CACHE.clear()
    q = "정화통 교체 주기"
    return rag.answer(q, rag.search(q, 3)), fake


def test_llm_answer(monkeypatch, rag):
    (out, mode), fake = run(monkeypatch, rag, "end_turn", "정화통은 6개월마다 교체합니다.\n")
    assert mode == "llm" and out == ["정화통은 6개월마다 교체합니다."]
    q = "정화통 교체 주기"
    rag.answer(q, rag.search(q, 3))
    assert fake.calls == 1                                   # 같은 질문은 저장된 답


def test_refusal_falls_back(monkeypatch, rag):
    (out, mode), _ = run(monkeypatch, rag, "refusal", "")
    assert mode == "extractive" and out
