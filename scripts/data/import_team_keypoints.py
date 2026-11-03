"""조원이 정리한 '문장별_수어키포인트' 폴더 → 동작사전 + 5문장 번역 메모리.

python scripts/data/import_team_keypoints.py ~/Downloads/문장별_수어키포인트 --out data/sign/motions
python scripts/data/import_team_keypoints.py ~/Downloads/문장별_통합정리 --merge     # 새 문장·새 단어만 더하기
python scripts/data/import_team_keypoints.py /없음 --extra ~/Downloads/단어수정/*.json --merge --suffix 1 --trim --no_sentences
                                                     # 단어 파일만: 이미 있는 단어는 그대로, 번호 붙이고, 앞뒤 쉬는 구간 자르기

폴더 구조 (조원 형식)
  01_위험장소_작업중지/sentence.json   {"sentence", "fps", "sequence": [{"order","gloss","file"}]}
  01_위험장소_작업중지/keypoints/01_위험.json

결과
  data/sign/motions/              글로스별 동작 (같은 글로스가 여러 문장에 있으면 처음 것 사용)
  data/sign/demo_sentences.json   문장 → 글로스 순서 (번역 메모리: 이 문장들은 모델 대신 확정 글로스 사용)
"""
import argparse
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict, bone_lengths, calibrate_expressions, lift_2d, load_team_clip, normalize, trim_rest  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("--extra", nargs="*", default=[], help="문장 폴더 밖의 단어 키포인트 JSON (조원 형식 2: gloss_raw 사용)")
ap.add_argument("--out", default="data/sign/motions")
ap.add_argument("--sentences_out", default="data/sign/demo_sentences.json")
ap.add_argument("--suffix", default="", help="번호 없는 글로스에 붙일 번호 (예: 1 → 교육 → 교육1)")
ap.add_argument("--trim", action="store_true", help="앞뒤 손 내리고 쉬는 구간 잘라내기 (사전 영상에서 잘라 온 단어)")
ap.add_argument("--no_sentences", action="store_true", help="문장 목록(demo_sentences.json)은 건드리지 않음")
ap.add_argument("--merge", action="store_true",
                help="이미 있는 것은 그대로: 동작사전에 없는 글로스만 저장, 문장은 기존 목록에 없는 것만 뒤에 붙임 "
                     "(기존 동작은 떨림·손 모양 보정이 맞춰져 있어 바꾸지 않음)")
a = ap.parse_args()

root = Path(a.root).expanduser()
sentences, report, clips = [], [], {}
def _num(p):                                           # '문장 12' 처럼 번호가 있으면 번호 순
    import re
    m = re.search(r"(\d+)", unicodedata.normalize("NFC", p.parent.name))
    return (int(m.group(1)) if m else 0, p.parent.name)


_md = MotionDict(a.out)
existing = _md.index if a.merge else {}
for sf in sorted(root.glob("*/sentence.json"), key=_num):
    meta = json.loads(sf.read_text(encoding="utf-8"))
    seq = sorted(meta["sequence"], key=lambda x: x.get("order", 0))
    folder = unicodedata.normalize("NFC", sf.parent.name)   # macOS 한글 파일명(NFD) 대응
    glosses = []
    for item in seq:
        g = item["gloss"]
        glosses.append(g)
        if g in clips or g in existing:
            continue
        frames, fps, dim = load_team_clip(sf.parent / item["file"])
        clips[g] = (frames, fps, dim, f"{folder}/{item['file']} ({dim}D)")
        report.append(f"  {g:<8} {len(frames):>3}프레임 {dim}D  ← {folder}")
    sentences.append({"korean": meta["sentence"], "gloss": glosses, "complete": "일부" not in folder,
                      "folder": folder})

import re  # noqa: E402
for f in a.extra:
    meta = json.loads(Path(f).read_text(encoding="utf-8"))
    g = unicodedata.normalize("NFC", meta.get("gloss_raw") or meta.get("gloss") or Path(f).stem)
    if a.suffix and not re.search(r"\d#?$", g):
        g += a.suffix
    if g in clips:
        continue
    if a.merge and _md.resolve(g):                              # 같은 단어(번호만 다른 것 포함)가 이미 있으면 그대로
        report.append(f"  {g:<8} 이미 있음({_md.resolve(g)}) → 그대로")
        continue
    frames, fps, dim = load_team_clip(f)
    clips[g] = (frames, fps, dim, f"{unicodedata.normalize('NFC', Path(f).name)} ({dim}D)")
    report.append(f"  {g:<8} {len(frames):>3}프레임 {dim}D  ← 추가 파일")

# 표정: 전체 클립 얼굴 특징 분포로 보정한 뒤 저장
calibrate_expressions([c[0] for c in clips.values()])
# 2D 자료는 3D 자료의 뼈 길이로 깊이를 추정해 3D 로 바꿔 저장
L = bone_lengths([normalize(c[0]) for c in clips.values() if c[2] == 3])
for g, (frames, fps, dim, src) in clips.items():
    fr = normalize(frames)
    if a.trim:
        n0 = len(fr)
        fr = trim_rest(fr)
        src = src.replace(")", f", {n0}→{len(fr)}프레임)", 1) if n0 != len(fr) else src
    if dim == 2:
        fr = lift_2d(fr, L)
        src = src.replace("(2D)", "(2D→3D)")
    MotionDict.save_clip(a.out, g, fr, round(fps), source=src)
saved = clips

Path(a.sentences_out).parent.mkdir(parents=True, exist_ok=True)
if a.no_sentences:
    sentences = json.loads(Path(a.sentences_out).read_text(encoding="utf-8")) if Path(a.sentences_out).exists() else []
elif a.merge and Path(a.sentences_out).exists():
    old = json.loads(Path(a.sentences_out).read_text(encoding="utf-8"))
    have = {s["korean"] for s in old}
    md = MotionDict(a.out)
    new = [dict(s, complete=all(md.get(g) for g in s["gloss"])) for s in sentences if s["korean"] not in have]
    sentences = old + new
    print(f"새 문장 {len(new)}개 추가")
Path(a.sentences_out).write_text(json.dumps(sentences, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"동작 {len(saved)}개 → {a.out}")
print("\n".join(report))
print(f"\n문장 {len(sentences)}개 → {a.sentences_out}")
for s in sentences:
    print(f"  {'완성' if s['complete'] else '일부'} | {s['korean']}  →  {' '.join(s['gloss'])}")
