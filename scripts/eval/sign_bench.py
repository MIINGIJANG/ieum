"""카메라 수어 입력 벤치마크: 낱말 하나씩 + 웹캠에서 흔한 문제를 흉내 낸 변형 9가지 (docs/sign_input.md).

동작 자료(data/sign/motions)를 웹캠 관절 점 모양으로 바꿔 가짜 녹화를 만들고, 실제 web/sign/core.js 를 브라우저에서 돌려
변형별 정확도를 표로 낸다 (scripts/eval/sign_bench.js, node + playwright 필요). 사람이 한 수어가 아니므로 실제 정확도는 아니다.

    uvicorn api:app --port 8000 &
    python scripts/eval/sign_bench.py                            # 기본 설정
    python scripts/eval/sign_bench.py '{"끔":{"mirror":false}}'  # 설정을 바꿔 비교 (core.js DEFAULTS 위에 덮어씀)
"""
import json, random, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ksl.motion import MotionDict
md = MotionDict(str(ROOT / 'data/sign/motions'))
dom = json.load(open(ROOT / 'web/sign/domain.json'))
base = lambda g: re.sub(r"[\d#]+$", "", g)
dl = set(dom['labels'])
POSE = {0: 0, 12: 2, 14: 3, 16: 4, 11: 5, 13: 6, 15: 7}
def P(p, s=0.2, ox=0.5, oy=0.42): return [ox + s * p[0], oy + s * p[1], s * (p[2] if len(p) > 2 else 0)]
def word_frames(g, s=0.2, ox=0.5, oy=0.42):
    clip = md.get(g); fps = clip.get('fps', 30); out = []; t = 0.0
    for f in clip['frames']:
        b = f.get('body')
        if not b or not b[1] or not b[2] or not b[5]: continue
        pose = [[ox, oy + 0.08, 0.0, 0.1] for _ in range(33)]
        for k, i in POSE.items():
            if b[i]: pose[k] = [*P(b[i], s, ox, oy), 0.99]
        pose[23] = [pose[11][0], pose[11][1] + 1.7 * s, 0.0, 0.9]; pose[24] = [pose[12][0], pose[11][1] + 1.7 * s, 0.0, 0.9]
        hand = lambda h: [P(p, s, ox, oy) for p in h] if h and all(h) else None
        out.append({'t': t, 'pose': pose, 'lh': hand(f.get('lh')), 'rh': hand(f.get('rh'))}); t += 1000 / fps
    return out
def finish(frames):
    """낱말 뒤에 손 내림 0.5초 (손 안 보이고 손목은 엉덩이 쪽으로)"""
    if not frames: return frames
    t = frames[-1]['t']; dt = 1000 / 30; last = frames[-1]
    for i in range(15):
        t += dt
        pose = [list(p) for p in last['pose']]
        for w, h in ((15, 23), (16, 24)): pose[w] = [pose[h][0], pose[h][1] + 0.02, 0.0, 0.9]
        frames.append({'t': t, 'pose': pose, 'lh': None, 'rh': None})
    return frames
def subsample(frames, k): return frames[::k]
def jitter(frames, rnd, sh=0.004, sp=0.002):
    out = []
    for f in frames:
        g = lambda p, s: [p[0] + rnd.gauss(0, s), p[1] + rnd.gauss(0, s), p[2] + rnd.gauss(0, s * 2)] + list(p[3:])
        out.append({'t': f['t'], 'pose': [g(p, sp) for p in f['pose']], 'lh': [g(p, sh) for p in f['lh']] if f['lh'] else None, 'rh': [g(p, sh) for p in f['rh']] if f['rh'] else None})
    return out
def dropout(frames, rnd, ms=(200, 350)):
    if len(frames) < 6: return frames
    dur = rnd.uniform(*ms); t0 = rnd.uniform(frames[1]['t'], max(frames[1]['t'] + 1, frames[-1]['t'] - dur - 60))
    return [{**f, 'lh': None, 'rh': None} if t0 <= f['t'] < t0 + dur else f for f in frames]
def mirror(frames):
    out = []
    for f in frames:
        m = lambda p: [1 - p[0], *p[1:]]
        pose = [m(p) for p in f['pose']]
        for a, b in ((11, 12), (13, 14), (15, 16), (23, 24)): pose[a], pose[b] = pose[b], pose[a]
        out.append({'t': f['t'], 'pose': pose, 'lh': [m(p) for p in f['rh']] if f['rh'] else None, 'rh': [m(p) for p in f['lh']] if f['lh'] else None})
    return out
def slow(frames, k):  # 천천히 한 수어 (시간만 k배)
    return [{**f, 't': f['t'] * k} for f in frames]
rnd = random.Random(0); recs = {}
for g in md.index:
    b = base(g)
    if not any((dom['words'].get(l) or base(l)) == b for l in dl): continue
    w = word_frames(g)
    if len(w) < 6 or not any(f['lh'] or f['rh'] for f in w): continue
    V = {
      'clean': lambda: w,
      'fps15': lambda: subsample(w, 2),
      'fps10': lambda: subsample(w, 3),
      'slow15': lambda: subsample(slow(w, 2), 1),       # 2배 천천히, 15fps 로 찍힘
      'jitter': lambda: jitter(w, rnd),
      'dropout': lambda: dropout(slow(w, 2), rnd),      # 천천히 하는 중에 손이 0.2~0.35초 안 잡힘
      'small': lambda: jitter(word_frames(g, 0.12, 0.62, 0.5), rnd, 0.003, 0.0015),
      'real': lambda: dropout(jitter(subsample(slow(word_frames(g, 0.15, 0.45, 0.45), 2), 1), rnd), rnd, (150, 300)),
      'left': lambda: mirror(w),
    }
    for name, fn in V.items():
        fr = finish([dict(f) for f in fn()])
        R = lambda p: [round(c, 4) for c in p]
        recs.setdefault(name, []).append({'word': b, 'variant': name, 'frames': [{'t': round(f['t']), 'pose': [R(p) for p in f['pose']], 'lh': [R(p) for p in f['lh']] if f['lh'] else None, 'rh': [R(p) for p in f['rh']] if f['rh'] else None} for f in fr]})
out = Path(tempfile.mkdtemp(prefix='sign-bench-'))
for name, rs in recs.items(): json.dump(rs, open(out / f'{name}.json', 'w'), ensure_ascii=False)
print(sum(map(len, recs.values())), '개 녹화,', len({r['word'] for rs in recs.values() for r in rs}), '낱말 →', out)
configs = sys.argv[1] if len(sys.argv) > 1 else '{"기본":{"session":4},"왼손잡이 대응 끔":{"mirror":false}}'
sys.exit(subprocess.call(['node', str(Path(__file__).with_suffix('.js')), str(out), configs, *sys.argv[2:3]]))
