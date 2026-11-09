"""SOS: 지금 위치에서 가장 가까운 응급실 + 자동차 경로.

- 응급실 위치·실시간 병상: 공공데이터포털 '국립중앙의료원 전국 응급의료기관 정보 조회 서비스' (키: DATA_GO_KR_KEY, 일반 인증키 Decoding)
- 자동차 경로: 카카오모빌리티 길찾기 (키: KAKAO_REST_KEY, 카카오 개발자 REST API 키)
키는 서버(환경 변수)에만 두고 브라우저에는 보내지 않음. 위치는 저장하지 않음 (같은 자리 질문은 1분만 메모리에 둠).
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

ERMCT = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"
KAKAO_ROUTE = "https://apis-navi.kakaomobility.com/v1/directions"
TIMEOUT = 8
_CACHE, _TTL = {}, 60

# ---------- 사용 기록·오류 (서버 메모리, /sos/status 로 봄). 위치·좌표는 남기지 않음 ----------
KST = timezone(timedelta(hours=9))
_now = lambda: datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
STATS = {"since": _now(), "requests": 0, "cache_hits": 0, "ok": 0, "no_er": 0, "failed": 0,
         "route_fail": 0, "beds_fail": 0, "last_ok": None, "last_fail": None, "ms_total": 0}
ERRORS = deque(maxlen=30)


def log_error(stage, err):
    """단계(nearby·route·beds·config)별 실패를 세고 최근 30건을 남김 (서버 로그에도 한 줄)."""
    msg = str(err)[:200]
    ERRORS.appendleft({"time": _now(), "stage": stage, "error": msg})
    if stage == "route":
        STATS["route_fail"] += 1
    elif stage == "beds":
        STATS["beds_fail"] += 1
    else:
        STATS["failed"] += 1
        STATS["last_fail"] = _now()
    print(f"[이음] SOS {stage} 실패: {msg}")


def status():
    done = STATS["ok"] + STATS["no_er"]
    return {**{k: v for k, v in STATS.items() if k != "ms_total"},
            "avg_ms": round(STATS["ms_total"] / done) if done else None,
            "configured": {k: bool(v) for k, v in configured().items()}, "recent_errors": list(ERRORS)}


def configured():
    return {"er": bool(os.environ.get("DATA_GO_KR_KEY")), "route": bool(os.environ.get("KAKAO_REST_KEY")),
            "kakao_js": os.environ.get("KAKAO_JS_KEY") or None}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _items(xml_bytes):
    """공공데이터포털 XML 응답 → [{태그: 글자}]"""
    root = ET.fromstring(xml_bytes)
    code = root.findtext(".//resultCode")
    if code not in (None, "00"):
        raise RuntimeError(f"공공데이터포털 오류 {code}: {root.findtext('.//resultMsg')}")
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


def _ermct(op, **params):
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise RuntimeError("DATA_GO_KR_KEY 없음")
    q = urllib.parse.urlencode({"serviceKey": key, **params})
    return _items(_get(f"{ERMCT}/{op}?{q}"))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _km(a_lat, a_lng, b_lat, b_lng):
    r = math.radians
    d = math.sin(r(b_lat - a_lat) / 2) ** 2 + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(r(b_lng - a_lng) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(d))


def nearby(lat, lng, n=5):
    """가까운 응급의료기관 n곳 (직선거리 순)."""
    out = []
    for it in _ermct("getEgytLcinfoInqire", WGS84_LAT=lat, WGS84_LON=lng, pageNo=1, numOfRows=n):
        la, lo = _num(it.get("latitude")), _num(it.get("longitude"))
        if la is None or lo is None:
            continue
        out.append({"hpid": it.get("hpid"), "name": it.get("dutyName"), "addr": it.get("dutyAddr"),
                    "tel": it.get("dutyTel1"), "lat": la, "lng": lo, "km": round(_km(lat, lng, la, lo), 2)})
    return sorted(out, key=lambda x: x["km"])


def beds(addr):
    """그 주소가 있는 시·군·구 응급실들의 실시간 병상 {hpid: {...}}"""
    parts = (addr or "").split()
    if not parts:
        return {}
    params = {"STAGE1": parts[0], "pageNo": 1, "numOfRows": 100}
    if len(parts) > 1 and parts[1][-1:] in ("시", "군", "구"):
        params["STAGE2"] = parts[1]
    res = {}
    for it in _ermct("getEmrrmRltmUsefulSckbdInfoInqire", **params):
        res[it.get("hpid")] = {"beds": _num(it.get("hvec")), "er_tel": it.get("dutyTel3") or None,
                               "ct": it.get("hvctayn") or None, "updated": it.get("hvidate") or None}
    return res


def route(lat, lng, to_lat, to_lng):
    """카카오모빌리티 자동차 경로: 거리(m)·시간(초)·경로 좌표 [[lat, lng], ...]"""
    key = os.environ.get("KAKAO_REST_KEY")
    if not key:
        return None
    q = urllib.parse.urlencode({"origin": f"{lng},{lat}", "destination": f"{to_lng},{to_lat}", "priority": "RECOMMEND"})
    data = json.loads(_get(f"{KAKAO_ROUTE}?{q}", {"Authorization": f"KakaoAK {key}"}))
    r = (data.get("routes") or [{}])[0]
    if r.get("result_code") not in (0, None):
        return None
    path = []
    for sec in r.get("sections", []):
        for road in sec.get("roads", []):
            v = road.get("vertexes", [])
            path += [[v[i + 1], v[i]] for i in range(0, len(v) - 1, 2)]
    s = r.get("summary", {})
    return {"distance": s.get("distance"), "duration": s.get("duration"), "path": path}


def nearest(lat, lng):
    """가까운 응급실 1·2순위(차로 빨리 닿는 순, 경로를 못 구하면 직선거리 순) + 경로 + 병상."""
    STATS["requests"] += 1
    key = (round(lat, 3), round(lng, 3))   # 약 100m 안의 같은 자리는 1분 동안 같은 답
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        STATS["cache_hits"] += 1
        return hit[1]
    t0 = time.time()
    try:
        cands = nearby(lat, lng, n=8)
    except Exception as e:
        log_error("nearby", e)
        raise
    if not cands:
        STATS["no_er"] += 1
        STATS["ms_total"] += int((time.time() - t0) * 1000)
        return {"er": None, "route": None, "ers": []}
    top = cands[:6]                           # 가까운 6곳은 차로 걸리는 시간으로 비교 (병상 있는 곳이 나올 때까지 더 보여 줄 수 있게)
    reg = lambda a: " ".join((a or "").split()[:2])          # 병상은 지역(시·도 + 시·군·구)마다 한 번에 받음
    regions = list(dict.fromkeys(reg(c["addr"]) for c in top))

    def safe(stage, f, *a):
        try:
            return f(*a)
        except Exception as e:
            log_error(stage, e)
            return None
    # 경로·병상 지역별 요청을 한꺼번에 (차례로 하면 몇 초씩 더 걸림)
    with ThreadPoolExecutor(max_workers=len(top) + len(regions)) as ex:
        routes = [ex.submit(safe, "route", route, lat, lng, c["lat"], c["lng"]) for c in top]
        bedf = {r: ex.submit(safe, "beds", beds, r) for r in regions}
        routes = [f.result() for f in routes]
        bedmap = {r: (f.result() or {}) for r, f in bedf.items()}
    # 차로 빨리 닿는 순서 (경로를 못 구한 곳은 직선거리 순으로 뒤에). 기본 2곳, 그 2곳 모두 응급실 병상이 없으면
    #  (병상 0 이하 또는 실시간 병상 정보에 없음) 병상 있는 곳이 나올 때까지 이어서 보여 줌
    order = sorted(zip(top, routes), key=lambda cr: (cr[1] is None or cr[1].get("duration") is None,
                                                     (cr[1] or {}).get("duration") or 0, cr[0]["km"]))
    allers = [{"er": {**c, **bedmap.get(reg(c["addr"]), {}).get(c["hpid"], {})}, "route": rt} for c, rt in order]
    has_bed = lambda x: (x["er"].get("beds") or 0) > 0
    n = 2
    while n < len(allers) and not any(has_bed(x) for x in allers[:n]):
        n += 1
    ers = allers[:n]
    out = {"er": ers[0]["er"], "route": ers[0]["route"], "ers": ers}
    STATS["ok"] += 1
    STATS["last_ok"] = _now()
    STATS["ms_total"] += int((time.time() - t0) * 1000)
    _CACHE[key] = (time.time(), out)
    if len(_CACHE) > 500:
        _CACHE.clear()
    return out
