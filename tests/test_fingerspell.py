"""지문자 (ksl/fingerspell.py): 자모 나누기, 손 점 만들기, 동작 이어붙이기에서 동작 없는 단어 대신 쓰기. 동작 없는 단어 기록."""
import json
import math

from ksl.fingerspell import frames_for, hand_points, jamo, load_table, spell_text
from ksl.missing import MissingLog
from ksl.motion import MotionDict, compose

from conftest import ROOT

T = load_table(ROOT / "data/sign/fingerspell.json")
md = MotionDict(ROOT / "data/sign/motions")


def test_jamo_and_text():
    assert jamo("정화통", T) == ["ㅈ", "ㅓ", "ㅇ", "ㅎ", "ㅗ", "ㅏ", "ㅌ", "ㅗ", "ㅇ"]   # 첫소리 ㅇ 은 뺌, ㅘ = ㅗ+ㅏ
    assert jamo("까", T) == ["ㄱ+", "ㅏ"]                                                # 쌍자음: ㄱ 을 옆으로
    assert spell_text("FS:정화통") == "정화통" and spell_text("때2") == "때" and spell_text("고정하다") == "고정"


def test_every_jamo_has_shape():
    for ch in "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅐㅒㅔㅖ":
        assert ch in T["shapes"], ch
    for parts in T["compose"].values():
        assert all(p.rstrip("+") in T["shapes"] for p in parts)


def test_hand_points_human_size():
    for name, sh in T["shapes"].items():
        pts, _ = hand_points(sh)
        assert len(pts) == 21
        L = math.dist(pts[0], pts[9])
        assert 0.2 < L < 0.3, name                                   # 손바닥 길이 (어깨너비 단위)
        for a, b in [(5, 6), (6, 7), (7, 8)]:
            assert 0.03 < math.dist(pts[a], pts[b]) < 0.16


def test_compose_uses_fingerspelling_for_missing():
    m = compose(["위험1", "FS:정화통", "없는말9"], md, fingerspell=T)
    s = m["segments"]
    assert s[0]["has_motion"] and not s[0].get("fingerspell")
    assert s[1]["fingerspell"] and s[1]["fs_text"] == "정화통" and s[1]["fs_verified"] is False
    assert all(m["frames"][i].get("fs") for i in range(s[1]["start"], s[1]["end"] + 1))
    assert s[2]["fingerspell"] and s[2]["fs_text"] == "없는말"
    assert not compose(["FS:정화통"], md)["segments"][0]["has_motion"]          # 표를 안 주면 예전처럼 회색


def test_missing_log(tmp_path):
    log = MissingLog(tmp_path / "m.json")
    log.record(["때2", "FS:정화통"], ["작업할 때 조심", "정화통을 바꾸세요"])
    log.record(["때2"], ["작업할 때 조심"])
    top = log.top()
    assert top[0]["gloss"] == "때2" and top[0]["count"] == 2 and top[0]["examples"] == ["작업할 때 조심"]
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["missing"]["FS:정화통"]["count"] == 1
    log.record_sub("안전모", ["머리0", "보호1"], "안전모를 쓰세요")
    sub = MissingLog(tmp_path / "m.json").top(subs=True)
    assert sub[0]["gloss"] == "안전모" and sub[0]["used"] == ["머리0", "보호1"]   # 대체 표현은 따로 기록
    assert MissingLog(tmp_path / "m.json").top()[0]["count"] == 2              # 다시 열어도 이어서


def test_missing_capture_replay(tmp_path):
    log = MissingLog(tmp_path / "m.json")
    with log.capture() as calls:                     # 미리 만든 답: 기록하지 않고 모아 둠
        log.record(["때2"], ["작업할 때 조심"])
        log.record_sub("안전모", ["머리0", "보호1"], "안전모를 쓰세요")
    assert not log.top() and not log.top(subs=True) and len(calls) == 2
    log.replay(calls)                                # 실제로 물어봤을 때 기록
    assert log.top()[0]["gloss"] == "때2" and log.top(subs=True)[0]["gloss"] == "안전모"
