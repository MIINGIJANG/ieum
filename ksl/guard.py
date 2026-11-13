"""남용 막기: 누구나 부를 수 있는 주소가 비용(Claude)·외부 한도(공공데이터·카카오)를 쓰므로, 한 곳에서 너무 많이 부르면
거절하지 않고 **비용이 들지 않는 방식으로** 답함 (질문: Claude 없이 문서 문장으로, SOS: 방금 찾아 둔 결과로).

- 요청 한도: 같은 곳(IP)에서 1분에 N번까지. IP 는 그대로 두지 않고 해시로만, 메모리에만 잠깐 둠 (기록·저장 안 함)
- Claude 하루 한도: 하루에 새 질문 LLM_DAILY_MAX 개(기본 2000)까지만 Claude 로 답하고, 넘으면 문서 문장을 그대로 뽑는 방식으로 답함
  (누가 일부러 계속 불러도 비용이 끝없이 늘지 않게)
평소 사용에는 걸리지 않음: 같은 질문은 캐시로 답하므로 한도에 세지 않음.
"""
import hashlib
import os
import threading
import time
from collections import deque

_lock = threading.Lock()
_tl = threading.local()            # 이 요청은 Claude 를 쓰지 않음 (한도를 넘은 곳의 요청)
_hits = {}                         # (종류, IP 해시) → 최근 요청 시각들
_day = {"date": None, "n": 0}
BLOCKED = {"rate": 0, "llm_budget": 0}   # 한도를 넘어 비용 없는 방식으로 답한 횟수 (/client/status)
MAX_TEXT = 300                     # 질문 글자 수 (넘는 부분은 버림)
MAX_GLOSSES = 200                  # 한 번에 요청하는 단어 수


def who(request):
    """요청한 곳: 프록시(HF Space) 뒤에서는 X-Forwarded-For 의 첫 주소. 해시로만 씀."""
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "")
    return hashlib.sha1(ip.encode()).hexdigest()[:16]


def allow(kind, who_, limit, per=60.0):
    """True 면 통과. 같은 곳에서 per 초 동안 limit 번까지."""
    now = time.monotonic()
    with _lock:
        if len(_hits) > 5000:                                  # 오래된 것 정리
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 600]:
                del _hits[k]
        q = _hits.setdefault((kind, who_), deque())
        while q and now - q[0] > per:
            q.popleft()
        if len(q) >= limit:
            BLOCKED["rate"] += 1
            return False
        q.append(now)
        return True


class no_llm:
    """with guard.no_llm(): … 안에서는 Claude 를 부르지 않음 (문서 문장을 뽑는 방식으로 답함)."""

    def __enter__(self):
        _tl.off = getattr(_tl, "off", 0) + 1

    def __exit__(self, *a):
        _tl.off -= 1


def llm_budget(spend=True):
    """지금 Claude 로 답해도 되는지 (요청 한도를 넘은 곳의 요청이 아니고, 하루 한도가 남았는지). spend=True 면 질문 하나를 셈."""
    if getattr(_tl, "off", 0):
        return False
    limit = int(os.environ.get("LLM_DAILY_MAX", "2000"))
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if _day["date"] != today:
            _day["date"], _day["n"] = today, 0
        if _day["n"] >= limit:
            if spend:
                BLOCKED["llm_budget"] += 1
            return False
        if spend:
            _day["n"] += 1
        return True


def status():
    with _lock:
        return {"blocked": dict(BLOCKED), "llm_questions_today": _day["n"], "llm_daily_max": int(os.environ.get("LLM_DAILY_MAX", "2000"))}
