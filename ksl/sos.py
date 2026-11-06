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
from concurrent.futures import ThreadPoolExecutor

ERMCT = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"
KAKAO_ROUTE = "https://apis-navi.kakaomobility.com/v1/directions"
TIMEOUT = 8
_CACHE, _TTL = {}, 60


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
    """가장 가까운 응급실(차로 가장 빨리 닿는 곳, 경로를 못 구하면 직선거리 가장 가까운 곳) + 경로 + 병상."""
    key = (round(lat, 3), round(lng, 3))   # 약 100m 안의 같은 자리는 1분 동안 같은 답
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    cands = nearby(lat, lng)
    if not cands:
        return {"er": None, "route": None}
    top = cands[:3]                           # 가까운 3곳은 차로 걸리는 시간으로 비교
    reg = lambda a: " ".join((a or "").split()[:2])          # 병상은 지역(시·도 + 시·군·구)마다 한 번에 받음
    regions = list(dict.fromkeys(reg(c["addr"]) for c in top))

    def safe(f, *a):
        try:
            return f(*a)
        except Exception:
            return None
    # 경로 3개·병상 지역별 요청을 한꺼번에 (차례로 하면 몇 초씩 더 걸림)
    with ThreadPoolExecutor(max_workers=len(top) + len(regions)) as ex:
        routes = [ex.submit(safe, route, lat, lng, c["lat"], c["lng"]) for c in top]
        bedf = {r: ex.submit(safe, beds, r) for r in regions}
        routes = [f.result() for f in routes]
        bedmap = {r: (f.result() or {}) for r, f in bedf.items()}
    best, best_route = cands[0], None
    for c, rt in zip(top, routes):
        if rt and rt.get("duration") is not None and (best_route is None or rt["duration"] < best_route["duration"]):
            best, best_route = c, rt
    best = {**best, **bedmap.get(reg(best["addr"]), {}).get(best["hpid"], {})}
    out = {"er": best, "route": best_route}
    _CACHE[key] = (time.time(), out)
    if len(_CACHE) > 500:
        _CACHE.clear()
    return out
