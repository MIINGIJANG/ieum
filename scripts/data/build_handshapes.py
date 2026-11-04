"""자주 나오는 손 모양(군집) 만들기 → web/handshapes.json

python scripts/data/build_handshapes.py --motions data/sign/motions --k 24

손 프레임마다 손가락 15개 마디의 굽힘 각도(도)를 구해 k-means 로 묶음.
아바타(web/avatar.js)는 손가락 굽힘을 이 각도(- base)로 정하고, 가장 가까운 손 모양 쪽으로 살짝(최대 35%) 끌어당김
→ 추정 떨림으로 생기는 불가능한 손 모양·꺾임이 줄어듦.
(언어학적으로 정리된 한국수어 수형 목록이 아니라 우리 동작 데이터에서 자주 나온 모양)
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict  # noqa: E402

CHAINS = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]]


def angles(h):
    """손 21점 → 15개 굽힘 각도(도). 앞 마디 방향과 이번 마디 방향 사이 각 (펴면 0)."""
    out = []
    for idx in CHAINS:
        chain = [0] + idx
        for j in range(3):
            a, b, c = h[chain[j]], h[chain[j + 1]], h[chain[j + 2]]
            if a is None or b is None or c is None:
                return None
            u = np.subtract(b, a)[:3]
            v = np.subtract(c, b)[:3]
            nu, nv = np.linalg.norm(u), np.linalg.norm(v)
            if nu < 1e-6 or nv < 1e-6:
                return None
            out.append(math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(u, v) / (nu * nv)))))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--motions", default="data/sign/motions")
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--out", default="web/handshapes.json")
    a = ap.parse_args()
    md = MotionDict(a.motions)
    X = []
    for g in md.index:
        clip = md.get(g)
        if not clip or str(clip.get("source", "")).endswith("(2D)"):
            continue
        for f in clip["frames"]:
            for k in ("rh", "lh"):
                h = f.get(k)
                if h and len(h) == 21 and all(p is not None and len(p) >= 3 for p in h):
                    x = angles(h)
                    if x:
                        X.append(x)
    X = np.array(X)
    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=a.k, n_init=10, random_state=0).fit(X)
    d = np.sqrt(((X - km.cluster_centers_[km.labels_]) ** 2).mean(axis=1))
    counts = np.bincount(km.labels_, minlength=a.k)
    order = np.argsort(-counts)
    res = {
        "about": "손가락 15마디 굽힘 각도(도) 군집. 순서: 엄지·검지·중지·약지·소지 × 뿌리·가운데·끝. scripts/data/build_handshapes.py",
        "frames": int(len(X)),
        "scale": round(float(np.median(d)), 2),
        # 손가락을 다 폈을 때도 마디 각도가 0 이 아님 (손바닥 점 위치 때문) → 마디마다 하위 10% 값을 '편 상태' 기준으로
        "base": [round(float(v), 1) for v in np.percentile(X, 10, axis=0)],
        "centers": [[round(float(v), 1) for v in km.cluster_centers_[i]] for i in order],
        "counts": [int(counts[i]) for i in order],
    }
    # 손가락 시너지 (Santello, Flanders & Soechting 1998 'Postural hand synergies'): 15마디 각도의 주성분.
    #  손 모양 대부분이 몇 개 성분의 조합으로 설명됨 → 아바타가 이 공간으로 살짝 끌어당겨 따로 노는 마디(추정 떨림)를 줄임
    mu = X.mean(axis=0)
    U, S_, Vt = np.linalg.svd(X - mu, full_matrices=False)
    var = (S_ ** 2) / (S_ ** 2).sum()
    k = int(np.searchsorted(np.cumsum(var), 0.9) + 1)                # 분산 90% 를 설명하는 성분 수
    res["synergy"] = {"mean": [round(float(v), 2) for v in mu], "comps": [[round(float(v), 4) for v in Vt[i]] for i in range(k)],
                      "var": [round(float(v), 3) for v in var[:k]]}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"손 프레임 {len(X)}개 → 손 모양 {a.k}개, 군집 안 평균 거리 {res['scale']}°, 시너지 {len(res['synergy']['comps'])}개"
          f"(분산 {sum(res['synergy']['var']):.0%}, 첫 둘 {sum(res['synergy']['var'][:2]):.0%}) → {a.out}")


if __name__ == "__main__":
    main()
