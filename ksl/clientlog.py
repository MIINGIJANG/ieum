"""화면(브라우저)에서 일어난 일 기록: 오류 건수와 첫 로딩에 걸린 시간 (GET /client/status 로 확인).

실제 기기에서 무엇이 실패하고 첫 로딩이 얼마나 걸리는지 알기 위한 것. 서버가 다시 켜지면 처음부터.
개인 정보는 남기지 않음: 주소(IP)·위치·질문 내용 없음, 기기는 '아이폰 Safari' 같은 큰 분류만, 오류 글은 주소 뒤 ?… 를 지우고 200자까지.
"""
import re
import threading
from collections import Counter, deque

_lock = threading.Lock()
ERRORS = Counter()                 # (종류, 기기) → 건수
RECENT = deque(maxlen=50)          # 최근 오류
BOOT = {}                          # 기기 → {단계: [ms…]} (기기마다 최근 200번)
STEPS = ("total", "avatar", "camera", "server", "examples", "answers", "fonts", "sos")
KINDS = ("error", "promise", "camera", "avatar", "query", "offline")


def device(ua):
    """User-Agent → 큰 분류만 ('아이폰 Safari', 'Android Chrome', 'PC Chrome' …). 원문은 남기지 않음."""
    ua = ua or ""
    os_ = "아이폰" if "iPhone" in ua else "아이패드" if "iPad" in ua else "Android" if "Android" in ua else "Mac" if "Macintosh" in ua else "PC"
    br = ("삼성 인터넷" if "SamsungBrowser" in ua else "Edge" if "Edg/" in ua else "Firefox" if "Firefox" in ua or "FxiOS" in ua
          else "Chrome" if "Chrome" in ua or "CriOS" in ua else "Safari" if "Safari" in ua else "기타")
    return f"{os_} {br}"


def _clean(msg):
    msg = re.sub(r"\?[^\s\"')]*", "", str(msg or ""))          # 주소 뒤 ?… (열쇠·좌표가 들어갈 수 있음)
    return re.sub(r"\s+", " ", msg).strip()[:200]


def log(body, ua=""):
    """{kind: 'boot', times: {단계: ms}} 또는 {kind: 오류 종류, msg, where}. 모르는 모양은 조용히 버림."""
    if not isinstance(body, dict):
        return
    dev, kind = device(ua), str(body.get("kind") or "")[:20]
    with _lock:
        if kind == "boot":
            t = body.get("times") if isinstance(body.get("times"), dict) else {}
            d = BOOT.setdefault(dev, {})
            for k in STEPS:
                v = t.get(k)
                if isinstance(v, (int, float)) and 0 <= v < 600_000:
                    d.setdefault(k, deque(maxlen=200)).append(int(v))
        elif kind in KINDS:
            ERRORS[(kind, dev)] += 1
            RECENT.append({"kind": kind, "device": dev, "where": _clean(body.get("where"))[:80], "msg": _clean(body.get("msg"))})


def _mid(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def status():
    with _lock:
        boot = {dev: {"count": len(d.get("total", ())), **{k: {"median_ms": _mid(v), "max_ms": max(v)} for k, v in d.items() if v}}
                for dev, d in BOOT.items()}
        errs = [{"kind": k, "device": dev, "count": n} for (k, dev), n in ERRORS.most_common()]
        return {"boot": boot, "errors": errs, "recent_errors": list(RECENT)}
