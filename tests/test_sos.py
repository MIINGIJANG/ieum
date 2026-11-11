"""SOS 가까운 응급실: 공공데이터포털·카카오 응답을 흉내 내서 고르기·병상·경로 합치기 확인 (실제 서버에는 요청 안 함)."""
import json

from ksl import sos

LOC = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A1</hpid><dutyName>먼병원</dutyName><dutyAddr>대구광역시 남구 현충로 170</dutyAddr><dutyTel1>053-1</dutyTel1><latitude>35.846</latitude><longitude>128.584</longitude></item>
<item><hpid>A2</hpid><dutyName>가까운병원</dutyName><dutyAddr>대구광역시 남구 어딘가 1</dutyAddr><dutyTel1>053-2</dutyTel1><latitude>35.8401</latitude><longitude>128.5801</longitude></item>
</items></body></response>"""
SER = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A2</hpid><MKioskTy20>Y</MKioskTy20><MKioskTy19>불가능</MKioskTy19><MKioskTy15>Y</MKioskTy15><MKioskTy15Msg>1500g 이상</MKioskTy15Msg><MKioskTy1>정보미제공</MKioskTy1><MKioskTy28>Y</MKioskTy28></item></items></body></response>"""
MSG = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A2</hpid><symBlkMsg>정형외과 전문의 부재로 수지접합 불가</symBlkMsg></item>
<item><hpid>A2</hpid><symBlkMsg>정형외과 전문의 부재로 수지접합 불가</symBlkMsg></item>
<item><hpid>A2</hpid><symBlkMsg>불가능메세지등록</symBlkMsg></item></items></body></response>"""
EMPTY = """<response><header><resultCode>00</resultCode></header><body><items></items></body></response>"""
BEDS = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A2</hpid><hvec>4</hvec><dutyTel3>053-119</dutyTel3><hvctayn>Y</hvctayn><hvidate>20261106163000</hvidate></item>
</items></body></response>"""


def fake_get(url, headers=None):
    if "getEgytLcinfoInqire" in url:
        return LOC.encode()
    if "getSrsillDissAceptncPosblInfoInqire" in url:   # 중증질환 수용: 가까운병원(A2) 사지접합 가능·중증화상 불가
        return SER.encode()
    if "getEmrrmSrsillDissMsgInqire" in url:   # 진료 제한 메시지: 가까운병원(A2)만
        return (MSG if "HPID=A2" in url else EMPTY).encode()
    if "getEgytListInfoInqire" in url:   # 응급의료기관 목록: 먼병원만 등급
        assert "Q0=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C" in url and "Q1=%EB%82%A8%EA%B5%AC" in url
        return """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A1</hpid><dutyEmclsName>권역응급의료센터</dutyEmclsName></item>
<item><hpid>A2</hpid><dutyEmclsName>지역응급의료기관</dutyEmclsName></item></items></body></response>""".encode()
    if "getEmrrmRltmUsefulSckbdInfoInqire" in url:
        assert "STAGE1=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C" in url and "STAGE2=%EB%82%A8%EA%B5%AC" in url
        return BEDS.encode()
    if "local/search/category" in url:   # 카카오 장소: 가까운병원 자리에 장례식장 + '가까운' 병원
        if "35.8401" in url:
            return json.dumps({"documents": [
                {"place_name": "가까운병원 장례식장", "category_name": "서비스 > 장례식장", "id": "9", "x": "128.5801", "y": "35.8401"},
                {"place_name": "가까운병원", "category_name": "의료,건강 > 병원", "id": "777", "x": "128.5802", "y": "35.8402"}]}).encode()
        return json.dumps({"documents": [{"place_name": "엉뚱한의원", "category_name": "의료,건강 > 병원", "id": "1", "x": "1", "y": "1"}]}).encode()
    if "kakaomobility" in url:
        assert headers["Authorization"] == "KakaoAK rest"
        far = "128.584" in url
        return json.dumps({"routes": [{"result_code": 0, "summary": {"distance": 900 if far else 400, "duration": 300 if far else 120},
                                       "sections": [{"roads": [{"vertexes": [128.58, 35.84, 128.5801, 35.8401]}]}]}]}).encode()
    raise AssertionError(url)


def test_nearest(monkeypatch):
    sos._PLACES.clear()
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setenv("KAKAO_REST_KEY", "rest")
    monkeypatch.setattr(sos, "_get", fake_get)
    sos._CACHE.clear()
    sos._GRADES.clear()
    out = sos.nearest(35.84, 128.58)
    assert out["ers"][1]["er"]["grade"] == "권역응급의료센터" and out["er"]["grade"] == "지역응급의료기관"
    assert out["er"]["name"] == "가까운병원" and out["er"]["beds"] == 4 and out["er"]["ct"] == "Y"
    assert out["er"]["kakao_id"] == "777" and out["er"]["lat"] == 35.8402          # 장례식장은 빼고 같은 이름 병원
    assert "kakao_id" not in out["ers"][1]["er"]                                     # 이름이 안 맞으면 원래 그대로
    assert out["er"]["msgs"] == ["정형외과 전문의 부재로 수지접합 불가"] and out["ers"][1]["er"]["msgs"] == []
    st = {x["name"]: (x["status"], x["msg"]) for x in out["er"]["serious"]}
    assert len(st) == 27 and st["저체중출생아 집중치료"] == ("가능", "1500g 이상") and st["수족지접합"] == ("가능", None)
    assert st["중증화상 전문치료"] == ("불가능", None) and st["심근경색 재관류중재술"] == ("정보 미제공", None) and st["분만"] == ("정보 미제공", None)
    other = out["ers"][1]["er"]["serious"]   # 수용 정보가 아예 없는 병원: 27개 모두 '정보 미제공'
    assert len(other) == 27 and {x["status"] for x in other} == {"정보 미제공"}
    assert out["route"]["duration"] == 120 and out["route"]["path"][0] == [35.84, 128.58]
    assert [x["er"]["name"] for x in out["ers"]] == ["가까운병원", "먼병원"] and out["ers"][1]["route"]["duration"] == 300


def test_without_route_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    monkeypatch.setattr(sos, "_get", fake_get)
    sos._CACHE.clear()
    out = sos.nearest(35.84, 128.58)
    assert out["er"]["name"] == "가까운병원" and out["route"] is None   # 직선거리로 가장 가까운 곳



def test_status_counts_failures(monkeypatch):
    """경로 요청이 실패하면 단계별로 세고 최근 오류에 남김 (좌표는 남기지 않음)."""
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setenv("KAKAO_REST_KEY", "rest")
    def bad_route(url, headers=None):
        if "kakaomobility" in url:
            raise OSError("timeout")
        return fake_get(url, headers)
    monkeypatch.setattr(sos, "_get", bad_route)
    sos._CACHE.clear(); sos.ERRORS.clear()
    before = dict(sos.STATS)
    out = sos.nearest(35.84, 128.58)
    st = sos.status()
    assert out["er"] and out["route"] is None
    assert st["route_fail"] - before["route_fail"] == 2 and st["ok"] - before["ok"] == 1
    assert st["recent_errors"][0]["stage"] == "route" and "35.84" not in str(st)


def test_more_until_bed(monkeypatch):
    """기본 2곳, 2곳 모두 병상이 없으면 병상 있는 곳이 나올 때까지 더 보여 줌."""
    loc = """<response><header><resultCode>00</resultCode></header><body><items>""" + "".join(
        f"<item><hpid>H{i}</hpid><dutyName>병원{i}</dutyName><dutyAddr>대구광역시 중구 길 {i}</dutyAddr><dutyTel1>053</dutyTel1>"
        f"<latitude>{35.84 + i * 0.001}</latitude><longitude>128.58</longitude></item>" for i in range(1, 6)) + "</items></body></response>"
    bed = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>H1</hpid><hvec>0</hvec></item><item><hpid>H4</hpid><hvec>3</hvec></item></items></body></response>"""
    def g(url, headers=None):
        return (loc if "Lcinfo" in url else bed).encode()
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    monkeypatch.setattr(sos, "_get", g)
    sos._CACHE.clear()
    names = [x["er"]["name"] for x in sos.nearest(35.84, 128.58)["ers"]]
    assert names == ["병원1", "병원2", "병원3", "병원4"]   # 병원4 에 병상이 있어 거기까지


def test_tie_more_beds_first(monkeypatch):
    """차로 걸리는 시간·거리가 같으면 병상이 많은 곳이 먼저."""
    loc = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>X</hpid><dutyName>가까운적은병상</dutyName><dutyAddr>대구광역시 중구 a</dutyAddr><latitude>35.841</latitude><longitude>128.58</longitude></item>
<item><hpid>Y</hpid><dutyName>먼많은병상</dutyName><dutyAddr>대구광역시 중구 b</dutyAddr><latitude>35.845</latitude><longitude>128.58</longitude></item>
</items></body></response>"""
    bed = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>X</hpid><hvec>2</hvec></item><item><hpid>Y</hpid><hvec>9</hvec></item></items></body></response>"""
    same = json.dumps({"routes": [{"result_code": 0, "summary": {"distance": 1000, "duration": 300}, "sections": []}]})
    def g(url, headers=None):
        return (same if "kakaomobility" in url else loc if "Lcinfo" in url else bed).encode()
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setenv("KAKAO_REST_KEY", "rest")
    monkeypatch.setattr(sos, "_get", g)
    sos._CACHE.clear()
    assert [x["er"]["name"] for x in sos.nearest(35.84, 128.58)["ers"]] == ["먼많은병상", "가까운적은병상"]


def test_error_log_hides_query():
    sos.ERRORS.clear()
    sos.log_error("beds", "fail https://apis.data.go.kr/x?serviceKey=SECRET&WGS84_LAT=35.1")
    assert "SECRET" not in str(sos.ERRORS) and "35.1" not in str(sos.ERRORS)


def test_excluded_hospital(monkeypatch):
    """MS재건병원은 목록에서 뺌 (가장 가까워도)."""
    loc = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>M</hpid><dutyName>MS재건병원</dutyName><dutyAddr>대구광역시 중구 a</dutyAddr><latitude>35.8401</latitude><longitude>128.58</longitude></item>
<item><hpid>K</hpid><dutyName>경북대학교병원</dutyName><dutyAddr>대구광역시 중구 b</dutyAddr><latitude>35.845</latitude><longitude>128.58</longitude></item>
</items></body></response>"""
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    monkeypatch.setattr(sos, "_get", lambda url, headers=None: (loc if "Lcinfo" in url else EMPTY).encode())
    sos._CACHE.clear()
    assert [x["er"]["name"] for x in sos.nearest(35.84, 128.58)["ers"]] == ["경북대학교병원"]


def test_only_designated(monkeypatch):
    """응급실운영신고기관은 빼고 지정 응급의료기관만 (가장 가까워도)."""
    loc = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>S</hpid><dutyName>정형외과병원</dutyName><dutyAddr>대구광역시 중구 a</dutyAddr><latitude>35.8401</latitude><longitude>128.58</longitude></item>
<item><hpid>K</hpid><dutyName>경북대학교병원</dutyName><dutyAddr>대구광역시 중구 b</dutyAddr><latitude>35.845</latitude><longitude>128.58</longitude></item>
</items></body></response>"""
    grd = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>S</hpid><dutyEmclsName>응급실운영신고기관</dutyEmclsName></item>
<item><hpid>K</hpid><dutyEmclsName>권역응급의료센터</dutyEmclsName></item></items></body></response>"""
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    monkeypatch.setattr(sos, "_get", lambda url, headers=None: (loc if "Lcinfo" in url else grd if "ListInfo" in url else EMPTY).encode())
    sos._CACHE.clear(); sos._GRADES.clear()
    ers = sos.nearest(35.84, 128.58)["ers"]
    assert [x["er"]["name"] for x in ers] == ["경북대학교병원"] and ers[0]["er"]["grade"] == "권역응급의료센터"
