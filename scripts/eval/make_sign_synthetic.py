"""카메라 수어 입력 평가용 '가짜 녹화' 만들기 → data/eval/sign_synthetic/syn_*.json

python scripts/eval/make_sign_synthetic.py

우리 아바타 동작 데이터(data/sign/motions)를 웹캠 관절 점 모양(이미지 좌표, MediaPipe 몸 33점·손 21점)으로 바꿔
예시 질문 5개를 낱말마다 '손 올림 → 수어 → 손 내림' 으로 이어 붙임. 평가 페이지(web/sign/eval.html)에 넣어
설정별 차이를 보는 용도 — **실제 사람의 웹캠 수어보다 훨씬 깨끗해서 점수가 높게 나옴** (실제 정확도 아님).
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ksl.motion import MotionDict  # noqa: E402

CASES = [("낙하물은 어떻게 조심해요?", ["물체", "떨어지다", "위험", "조심"]),
         ("감전 위험이 있으면 뭘 착용해야 해요?", ["감전", "위험", "입다"]),
         ("보호구에 이상이 있는지 어떻게 확인해요?", ["보호", "이상하다", "확인"]),
         ("보호구 세척은 어떻게 해요?", ["보호", "씻다", "어떻게"]),
         ("2미터 넘는 높은 곳에서는 어떤 보호구를 착용해요?", ["높다", "장소", "보호", "입다"])]
POSE = {0: 0, 12: 2, 14: 3, 16: 4, 11: 5, 13: 6, 15: 7}     # MediaPipe 몸 번호 ← 우리 body 8점 (코, 목, 오른어깨…)


def main(out=ROOT / "data/eval/sign_synthetic"):
    md = MotionDict(ROOT / "data/sign/motions")
    meta = json.loads((ROOT / "web/sign/models/meta.json").read_text(encoding="utf-8"))
    base = lambda g: re.sub(r"[\d#]+$", "", g)           # noqa: E731
    known = {base(x) for x in meta["labels"]}
    P = lambda p, s=0.2: (0.5 + s * p[0], 0.42 + s * p[1], s * (p[2] if len(p) > 2 else 0))   # noqa: E731  목 원점·어깨너비 1 → 이미지 좌표
    random.seed(0)
    out.mkdir(parents=True, exist_ok=True)
    for qi, (q, ws) in enumerate(CASES):
        frames, t, used = [], 0.0, []
        for w in ws:
            g = md.resolve(w + "1") or md.resolve(w)
            if not g or w not in known:
                continue
            clip = md.get(g)
            fps = clip.get("fps", 30)
            used.append(w)
            for f in clip["frames"]:
                b = f.get("body")
                if not b or not b[1] or not b[2] or not b[5]:
                    continue
                pose = [[0.5, 0.5, 0.0, 0.1] for _ in range(33)]
                for k, i in POSE.items():
                    if b[i]:
                        pose[k] = [*P(b[i]), 0.99]
                pose[23] = [pose[11][0], pose[11][1] + 0.34, 0.0, 0.9]     # 엉덩이: 어깨 아래 1.7 어깨너비
                pose[24] = [pose[12][0], pose[11][1] + 0.34, 0.0, 0.9]
                hand = lambda h: [[round(c + (random.gauss(0, 0.002) if j < 2 else 0), 4) for j, c in enumerate(P(p))] for p in h] if h and all(h) else None   # noqa: E731
                frames.append({"t": round(t), "pose": [[round(c, 4) for c in p] for p in pose], "lh": hand(f.get("lh")), "rh": hand(f.get("rh"))})
                t += 1000 / fps
            for _ in range(12):                                            # 손을 내림 (0.4초 동안 손 안 보임)
                frames.append({**frames[-1], "t": round(t), "lh": None, "rh": None})
                t += 1000 / 30
        data = {"version": 1, "engine": "synthetic", "label": {"question": q, "words": " ".join(used)}, "frames": frames}
        (out / f"syn_{qi}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"syn_{qi}.json  {q}  낱말 {' '.join(used)}  {len(frames)}프레임")


if __name__ == "__main__":
    main()
