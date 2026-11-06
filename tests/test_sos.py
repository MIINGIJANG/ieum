"""SOS 가까운 응급실: 공공데이터포털·카카오 응답을 흉내 내서 고르기·병상·경로 합치기 확인 (실제 서버에는 요청 안 함)."""
import json

from ksl import sos

LOC = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A1</hpid><dutyName>먼병원</dutyName><dutyAddr>대구광역시 남구 현충로 170</dutyAddr><dutyTel1>053-1</dutyTel1><latitude>35.846</latitude><longitude>128.584</longitude></item>
<item><hpid>A2</hpid><dutyName>가까운병원</dutyName><dutyAddr>대구광역시 남구 어딘가 1</dutyAddr><dutyTel1>053-2</dutyTel1><latitude>35.8401</latitude><longitude>128.5801</longitude></item>
</items></body></response>"""
BEDS = """<response><header><resultCode>00</resultCode></header><body><items>
<item><hpid>A2</hpid><hvec>4</hvec><dutyTel3>053-119</dutyTel3><hvctayn>Y</hvctayn><hvidate>20261106163000</hvidate></item>
</items></body></response>"""


def fake_get(url, headers=None):
    if "getEgytLcinfoInqire" in url:
        return LOC.encode()
    if "getEmrrmRltmUsefulSckbdInfoInqire" in url:
        assert "STAGE1=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C" in url and "STAGE2=%EB%82%A8%EA%B5%AC" in url
        return BEDS.encode()
    if "kakaomobility" in url:
        assert headers["Authorization"] == "KakaoAK rest"
        far = "128.584" in url
        return json.dumps({"routes": [{"result_code": 0, "summary": {"distance": 900 if far else 400, "duration": 300 if far else 120},
                                       "sections": [{"roads": [{"vertexes": [128.58, 35.84, 128.5801, 35.8401]}]}]}]}).encode()
    raise AssertionError(url)


def test_nearest(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setenv("KAKAO_REST_KEY", "rest")
    monkeypatch.setattr(sos, "_get", fake_get)
    sos._CACHE.clear()
    out = sos.nearest(35.84, 128.58)
    assert out["er"]["name"] == "가까운병원" and out["er"]["beds"] == 4 and out["er"]["ct"] == "Y"
    assert out["route"]["duration"] == 120 and out["route"]["path"][0] == [35.84, 128.58]


def test_without_route_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    monkeypatch.setattr(sos, "_get", fake_get)
    sos._CACHE.clear()
    out = sos.nearest(35.84, 128.58)
    assert out["er"]["name"] == "가까운병원" and out["route"] is None   # 직선거리로 가장 가까운 곳

