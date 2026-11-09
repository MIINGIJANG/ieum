"""SOS: 지금 위치에서 가장 가까운 응급실 + 자동차 경로.

- 응급실 위치·실시간 병상: 공공데이터포털 '국립중앙의료원 전국 응급의료기관 정보 조회 서비스' (키: DATA_GO_KR_KEY, 일반 인증키 Decoding)
- 자동차 경로: 카카오모빌리티 길찾기 (키: KAKAO_REST_KEY, 카카오 개발자 REST API 키)
키는 서버(환경 변수)에만 두고 브라우저에는 보내지 않음. 위치는 저장하지 않음 (같은 자리 질문은 1분만 메모리에 둠).
"""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

ERMCT = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"
KAKAO_ROUTE = "https://apis-navi.kakaomobility.com/v1/directions"
KAKAO_PLACE = "https://dapi.kakao.com/v2/local/search/category.json"
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
    msg = re.sub(r"\?\S*", "?…", str(err))[:200]   # 주소 뒤 ?… (키·좌표) 는 지우고 남김
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


# 지정 응급의료기관 등급 (이 등급만 목록에 보여 줌)
DESIGNATED = {"권역응급의료센터", "전문응급의료센터", "지역응급의료센터", "지역응급의료기관"}

# 목록에서 빼는 병원 (이름, 띄어쓰기·기호 무시하고 비교)
EXCLUDE = {"MS재건병원", "바로본병원"}


def nearby(lat, lng, n=5):
    """가까운 응급의료기관 n곳 (직선거리 순). EXCLUDE 병원은 빼고 그만큼 더 받음."""
    out, skip = [], {"".join(ch for ch in x if ch.isalnum()) for x in EXCLUDE}
    for it in _ermct("getEgytLcinfoInqire", WGS84_LAT=lat, WGS84_LON=lng, pageNo=1, numOfRows=n + len(EXCLUDE)):
        la, lo = _num(it.get("latitude")), _num(it.get("longitude"))
        if la is None or lo is None or "".join(ch for ch in (it.get("dutyName") or "") if ch.isalnum()) in skip:
            continue
        out.append({"hpid": it.get("hpid"), "name": it.get("dutyName"), "addr": it.get("dutyAddr"),
                    "tel": it.get("dutyTel1"), "grade": it.get("dutyEmclsName") or None,   # 권역·지역응급의료센터, 지역응급의료기관
                    "lat": la, "lng": lo, "km": round(_km(lat, lng, la, lo), 2)})
    return sorted(out, key=lambda x: x["km"])[:n]


def messages(hpid):
    """병원이 직접 올린 응급실·중증질환 진료 제한 메시지 (예: '정형외과 전문의 부재로 수지접합 불가'). 최대 3개, 같은 문장은 한 번."""
    out = []
    for it in _ermct("getEmrrmSrsillDissMsgInqire", HPID=hpid, pageNo=1, numOfRows=10):
        m = (it.get("symBlkMsg") or "").strip()
        if m and m not in out:
            out.append(m)
    return out[:3]


def _region(addr):
    parts = (addr or "").split()
    if not parts:
        return None
    params = {"STAGE1": parts[0], "pageNo": 1, "numOfRows": 100}
    if len(parts) > 1 and parts[1][-1:] in ("시", "군", "구"):
        params["STAGE2"] = parts[1]
    return params


# 중증질환자 수용 가능 정보 (getSrsillDissAceptncPosblInfoInqire) 항목 → 화면 이름.
#  국립중앙의료원 OpenAPI 활용가이드 V13 「응답 메시지 명세」의 이름 (값: Y 가능, 불가능, 정보미제공. MKioskTy28 응급실 gate keeper 는 질환이 아니라 뺌)
SERIOUS = {f"MKioskTy{i}": n for i, n in enumerate([
    "심근경색 재관류중재술", "뇌경색 재관류중재술", "거미막하출혈 수술", "거미막하출혈 외 뇌출혈 수술",
    "흉부 대동맥응급", "복부 대동맥응급", "담낭질환", "담도포함질환", "비외상 복부응급수술", "영유아 장중첩·폐색",
    "성인 위장관 응급내시경", "영유아 위장관 응급내시경", "성인 기관지 응급내시경", "영유아 기관지 응급내시경",
    "저체중출생아 집중치료", "분만", "산과수술", "부인과수술", "중증화상 전문치료", "수족지접합", "수족지접합 외 사지접합",
    "응급투석(HD)", "응급투석(CRRT)", "정신과 폐쇄병동입원", "안과 응급수술", "성인 영상의학혈관중재", "영유아 영상의학혈관중재"], 1)}


def serious(addr):
    """그 시·군·구 응급실들의 중증질환 수용 가능 여부 {hpid: {"가능": [...], "불가": [...]}} (Y 가능, 불가능 불가, 정보미제공은 뺌)"""
    params = _region(addr)
    if not params:
        return {}
    res, seen, keys = {}, False, None
    for it in _ermct("getSrsillDissAceptncPosblInfoInqire", **params):
        keys = keys or sorted(it)
        ok, no = [], []
        for k, name in SERIOUS.items():
            v = (it.get(k) or "").strip().upper()
            if v:
                seen = True
            label = name + (f" ({it.get(k + 'Msg').strip()})" if (it.get(k + "Msg") or "").strip() else "")   # 예: 저체중출생아 집중치료 (1500g 이상, 30주 이상)
            if v.startswith("Y"):
                ok.append(label)
            elif v.startswith("N") or "불가" in v:
                no.append(label)
        res[it.get("hpid")] = {"가능": ok, "불가": no}
    if res and not seen:   # 항목 이름이 예상과 다르면 기록 (어떤 항목이 왔는지만, 값·좌표 없이)
        log_error("serious", "예상한 MKioskTy 항목 없음, 받은 항목: " + ",".join(keys or []))
    return res


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


_GRADES = {}   # 지역 → (받은 시각, {hpid: 등급}). 응급의료기관 지정은 잘 안 바뀌므로 하루 동안 기억


def grades(addr):
    """그 시·군·구 응급의료기관 등급 {hpid: '권역응급의료센터' 등} (응급의료기관 목록정보 조회의 dutyEmclsName)"""
    parts = (addr or "").split()
    if not parts:
        return {}
    key = " ".join(parts[:2])
    hit = _GRADES.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    params = {"Q0": parts[0], "pageNo": 1, "numOfRows": 100}
    if len(parts) > 1 and parts[1][-1:] in ("시", "군", "구"):
        params["Q1"] = parts[1]
    res = {it.get("hpid"): (it.get("dutyEmclsName") or "").strip() for it in _ermct("getEgytListInfoInqire", **params)}
    res = {h: g for h, g in res.items() if h and g}
    _GRADES[key] = (time.time(), res)
    return res


_PLACES = {}   # hpid → 카카오 장소 (병원 위치·이름은 잘 안 바뀌므로 서버가 켜져 있는 동안 기억)
_squash = lambda t: "".join(ch for ch in (t or "") if ch.isalnum())


def place(c):
    """공공데이터포털 병원과 같은 카카오맵 장소 {name, id, lat, lng} (확실하지 않으면 None).
    그 좌표 200m 안의 '병원'(HP8) 장소 중 이름이 공식 이름 안에 들어 있거나(예: 바로본병원 ⊂ 의료법인백천의료재단바로본병원)
    공식 이름을 담은 것만 같은 병원으로 봄. 장례식장·주차장은 뺌."""
    if c["hpid"] in _PLACES:
        return _PLACES[c["hpid"]]
    key = os.environ.get("KAKAO_REST_KEY")
    if not key:
        return None
    q = urllib.parse.urlencode({"category_group_code": "HP8", "x": c["lng"], "y": c["lat"], "radius": 200, "sort": "distance"})
    docs = json.loads(_get(f"{KAKAO_PLACE}?{q}", {"Authorization": f"KakaoAK {key}"})).get("documents", [])
    raw, best = _squash(c["name"]), None
    for d in docs:
        nm, cat = d.get("place_name", ""), d.get("category_name", "")
        if any(w in nm + cat for w in ("장례식장", "주차장")):
            continue
        k = _squash(nm)
        if not k:
            continue
        # 같음 > 카카오 이름 ⊂ 공식 이름(긴 것) > 공식 이름 ⊂ 카카오 이름(짧은 것)
        score = (3, 0) if k == raw else (2, len(k)) if k in raw else (1, -len(k)) if raw in k else None
        if score and (best is None or score > best[0]):
            best = (score, {"name": nm, "id": d.get("id"), "lat": _num(d.get("y")), "lng": _num(d.get("x"))})
    out = best[1] if best and best[1]["lat"] is not None and best[1]["lng"] is not None else None
    _PLACES[c["hpid"]] = out
    return out


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


def nearest(lat, lng, fresh=False):
    """가까운 응급실 1·2순위(차로 빨리 닿는 순, 경로를 못 구하면 직선거리 순) + 경로 + 병상."""
    STATS["requests"] += 1
    key = (round(lat, 3), round(lng, 3))   # 약 100m 안의 같은 자리는 1분 동안 같은 답
    hit = _CACHE.get(key)
    if hit and not fresh and time.time() - hit[0] < _TTL:   # fresh: SOS 팝업을 새로 열 때는 늘 새로 조회
        STATS["cache_hits"] += 1
        return hit[1]
    t0 = time.time()

    def safe(stage, f, *a):
        try:
            return f(*a)
        except Exception as e:
            log_error(stage, e)
            return None
    try:
        cands = nearby(lat, lng, n=16)       # 지정 응급의료기관만 남기므로 넉넉히 받음
    except Exception as e:
        log_error("nearby", e)
        raise
    reg = lambda a: " ".join((a or "").split()[:2])          # 병상·등급은 지역(시·도 + 시·군·구)마다 한 번에 받음
    # 지정 응급의료기관(권역·전문·지역응급의료센터, 지역응급의료기관)만. 응급실운영신고기관(정형외과 전문병원 등)은 뺌
    #  그 지역 등급 정보를 아예 못 받았으면(조회 실패·빈 응답) 그 지역은 거르지 않음
    cregions = list(dict.fromkeys(reg(c["addr"]) for c in cands))
    with ThreadPoolExecutor(max_workers=max(1, len(cregions))) as ex:
        grdmap = dict(zip(cregions, ex.map(lambda r: safe("grades", grades, r) or {}, cregions)))
    def designated(c):
        g = grdmap.get(reg(c["addr"])) or {}
        if not g:
            return True
        c["grade"] = c.get("grade") or g.get(c["hpid"]) or None
        return c["grade"] in DESIGNATED
    cands = [c for c in cands if designated(c)]
    if not cands:
        STATS["no_er"] += 1
        STATS["ms_total"] += int((time.time() - t0) * 1000)
        return {"er": None, "route": None, "ers": []}
    top = cands[:6]                           # 가까운 6곳은 차로 걸리는 시간으로 비교 (병상 있는 곳이 나올 때까지 더 보여 줄 수 있게)
    regions = list(dict.fromkeys(reg(c["addr"]) for c in top))
    def placed_route(c):
        """카카오맵 장소를 찾아 이름·위치를 맞춘 뒤(못 찾으면 원래 그대로) 그 위치까지 자동차 경로"""
        pl = safe("place", place, c)
        if pl:
            c.update({"official": c["name"], "name": pl["name"], "kakao_id": pl["id"], "lat": pl["lat"], "lng": pl["lng"]})
        return route(lat, lng, c["lat"], c["lng"])
    # 카카오 장소·경로, 병상 지역별, 진료 제한 메시지 요청을 한꺼번에 (차례로 하면 몇 초씩 더 걸림)
    with ThreadPoolExecutor(max_workers=2 * len(top) + 2 * len(regions)) as ex:
        routes = [ex.submit(safe, "route", placed_route, c) for c in top]
        bedf = {r: ex.submit(safe, "beds", beds, r) for r in regions}
        serf = {r: ex.submit(safe, "serious", serious, r) for r in regions}
        msgf = {c["hpid"]: ex.submit(safe, "messages", messages, c["hpid"]) for c in top}
        routes = [f.result() for f in routes]
        bedmap = {r: (f.result() or {}) for r, f in bedf.items()}
        msgs = {h: (f.result() or []) for h, f in msgf.items()}
        sermap = {r: (f.result() or {}) for r, f in serf.items()}
    # 차로 빨리 닿는 순서 (경로를 못 구한 곳은 직선거리 순으로 뒤에). 기본 2곳, 그 2곳 모두 응급실 병상이 없으면
    #  (병상 0 이하 또는 실시간 병상 정보에 없음) 병상 있는 곳이 나올 때까지 이어서 보여 줌
    #  시간·거리(경로)가 같으면 응급실 남은 병상이 많은 곳 먼저, 그것도 같으면 직선거리 가까운 곳 먼저
    bed_of = lambda c: bedmap.get(reg(c["addr"]), {}).get(c["hpid"], {}).get("beds") or 0
    def rank(cr):
        c, rt = cr
        if not rt or rt.get("duration") is None:   # 경로를 못 구한 곳: 뒤로, 직선거리 순
            return (1, c["km"], 0, 0, 0)
        return (0, rt["duration"], rt.get("distance") or 0, -bed_of(c), c["km"])
    order = sorted(zip(top, routes), key=rank)
    allers = [{"er": {**c, **bedmap.get(reg(c["addr"]), {}).get(c["hpid"], {}), "msgs": msgs.get(c["hpid"], []),
                      "serious": sermap.get(reg(c["addr"]), {}).get(c["hpid"])}, "route": rt}
              for c, rt in order]
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
