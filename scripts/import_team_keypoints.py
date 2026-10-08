"""조원이 정리한 '문장별_수어키포인트' 폴더 → 동작사전 + 5문장 번역 메모리.

python scripts/import_team_keypoints.py ~/Downloads/문장별_수어키포인트 --out data/motions

폴더 구조 (조원 형식)
  01_위험장소_작업중지/sentence.json   {"sentence", "fps", "sequence": [{"order","gloss","file"}]}
  01_위험장소_작업중지/keypoints/01_위험.json

결과
  data/motions/              글로스별 동작 (같은 글로스가 여러 문장에 있으면 처음 것 사용)
  data/demo_sentences.json   문장 → 글로스 순서 (번역 메모리: 이 문장들은 모델 대신 확정 글로스 사용)
"""
import argparse
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksl.motion import MotionDict, calibrate_expressions, load_team_clip, normalize  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("--out", default="data/motions")
ap.add_argument("--sentences_out", default="data/demo_sentences.json")
a = ap.parse_args()

root = Path(a.root).expanduser()
sentences, report, clips = [], [], {}
for sf in sorted(root.glob("*/sentence.json")):
    meta = json.loads(sf.read_text(encoding="utf-8"))
    seq = sorted(meta["sequence"], key=lambda x: x.get("order", 0))
    folder = unicodedata.normalize("NFC", sf.parent.name)   # macOS 한글 파일명(NFD) 대응
    glosses = []
    for item in seq:
        g = item["gloss"]
        glosses.append(g)
        if g in clips:
            continue
        frames, fps, dim = load_team_clip(sf.parent / item["file"])
        clips[g] = (frames, fps, dim, f"{folder}/{item['file']} ({dim}D)")
        report.append(f"  {g:<8} {len(frames):>3}프레임 {dim}D  ← {folder}")
    sentences.append({"korean": meta["sentence"], "gloss": glosses, "complete": "일부" not in folder,
                      "folder": folder})

# 표정: 전체 클립 얼굴 특징 분포로 보정한 뒤 저장
calibrate_expressions([c[0] for c in clips.values()])
for g, (frames, fps, dim, src) in clips.items():
    MotionDict.save_clip(a.out, g, normalize(frames), round(fps), source=src)
saved = clips

Path(a.sentences_out).parent.mkdir(parents=True, exist_ok=True)
Path(a.sentences_out).write_text(json.dumps(sentences, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"동작 {len(saved)}개 → {a.out}")
print("\n".join(report))
print(f"\n문장 {len(sentences)}개 → {a.sentences_out}")
for s in sentences:
    print(f"  {'완성' if s['complete'] else '일부'} | {s['korean']}  →  {' '.join(s['gloss'])}")
