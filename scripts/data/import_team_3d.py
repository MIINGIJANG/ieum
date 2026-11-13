"""팀원 '데이터_전달자료_3D처리본' 폴더 → 동작사전 (이미 있는 단어는 그대로 두고 새 단어만 더함).

python scripts/data/import_team_3d.py ~/Downloads/데이터_전달자료_3D처리본_20261103
python scripts/data/import_team_3d.py ~/Downloads/KOSHA_추가수어자료_3D형식      (자료목록.json 이 있는 전달본, 2026-11-05)

폴더 구조 (README.md 참고)
  처리현황.csv                 단어마다 파일·출처·service_use_policy (review_only 는 가져오지 않음)
    또는 자료목록.json          {"items": [{word, file, playback_state, source_mode, source_id, gloss_raw, service_use_policy}]}
  좌표/<단어>.json             {"gloss","fps","landmarks": {pose_keypoints_3d, face_keypoints_3d, hand_*_keypoints_3d}} 평탄 x,y,z
  재생상태/<단어>.json         point_status(0 = 관측 안 됨, 채운 값), hand_rest_weight, face_enabled_frames
좌표: 클립 어깨 중점 원점, 어깨너비 단위, +X 화면 오른쪽, +Y 위, +Z 카메라 쪽 → 이 저장소 기준(+Y 아래, +Z 카메라에서 멀어짐)으로 뒤집음

처리
- 재생상태를 함께 적용: 상태 0 인 점은 없음(None), hand_rest_weight 0.5 이상인 손은 없음 → 아바타가 힘 뺀 손으로
  얼굴은 face_enabled_frames 에서만
- MediaPipe 에서 온 깊이(z)는 x·y 보다 크게 나오는 경우가 있어(손목이 몸 앞 3 어깨너비 이상) 팔·손가락 뼈 길이가
  기존 3D 동작과 맞도록 깊이 배율을 클립마다 맞춤 (ksl/motion.py load_mediapipe_avatar 와 같은 방식)
- MediaPipe·후처리본은 어깨가 좁게 잡혀 몸이 크게 나오므로 목→코 높이를 AI Hub 3D 중앙값에 맞춤
- 앞뒤 손 내리고 쉬는 구간은 잘라냄 (trim_rest)
- 검수에서 뺀 단어(ksl/motion.py REMOVED)와 이미 있는 단어(번호만 다른 것 포함)는 건드리지 않음
"""
import argparse
import csv
import json
import math
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import (HAND_BONES, REMOVED, MotionDict, _strip_num, bone_lengths, calibrate_expressions,  # noqa: E402
                        normalize, trim_rest)

# 폴더의 단어 이름 → 글로스 (같은 뜻 두 이름이 묶인 것은 앞 이름)
NAMES = {"근로자_직원": "근로자1", "작업_근무": "작업1", "늘, 항시": "항시1"}
# 가져오지 않는 단어 (아바타로 하나씩 재생해 보고 정함)
SKIP = {
    "문서": "두 손이 머리 위로 크게 올라감 (깊이 추정 오차로 보임)",
    "동안": "두 팔이 머리 위로 벌어짐 (문장에서 잘라 온 구간의 앞뒤 움직임으로 보임)",
    "안전모": "두 손이 머리 옆이 아니라 머리 위 바깥으로 감 (얼굴 깊이가 프레임마다 크게 흔들림). 대체 표현(머리 보호) 유지",
    "근로자_직원": "오른손목이 뒤로 크게 꺾여 어색함 (사용자 요청으로 뺌)",
    "작업_근무": "'작업'은 이미 일하다1 수어로 보여줌",
    "발생": "'발생'은 이미 일어나다0 수어로 보여줌",
    "주의": "'주의'는 이미 조심1 수어로 보여줌",
    # 2026-11-05 추가 전달본 (자료목록.json): 값으로 점검해서 뺌
    "다리": "손이 어깨너비 1.9배 옆으로 벌어짐 (보통 0.9 이하)",
    "모두": "손이 코보다 어깨너비 1.6배 위로 올라감 (손목 연결 이상치 프레임)",
    "수거": "손이 어깨너비 1.7배 옆으로 벌어짐 (손목 연결 이상치 프레임)",
    "양": "손이 코보다 0.55 위 (기존 동작은 거의 코 높이 이하), '양(분량)'과 '양(동물)' 구분 필요",
    "진동": "손이 머리 위·옆으로 크게 벌어짐, 출처 문장의 '진동'은 지명(진동지점)",
    "잠수": "출처 문장의 '잠수'는 다리 이름(잠수교) → 뜻 검수 필요",
    "위": "'위(위쪽)'와 '위(배 속)' 구분 필요, 출처 문장에 '위' 가 없음",
    "돌": "'돌(바위)'과 '돌(첫돌)' 구분 필요, 출처 문장에 '돌' 이 없음",
    "코": "'코(신체)' 와 검사 문장의 다른 뜻 구분 필요",
    "밀폐": "아바타로 재생해 보니 팔이 머리 위로 크게 벌어짐 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "덮다": "아바타로 재생해 보니 두 팔을 벌린 채 굳어 있음 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "보다": "아바타로 재생해 보니 한 손이 이마를 덮고 다른 손이 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "동시": "아바타로 재생해 보니 두 팔이 머리 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "바닥": "아바타로 재생해 보니 끝에서 팔이 머리 위로 올라감 ('바닥'은 낮은 곳) (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "박스": "아바타로 재생해 보니 손이 머리 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "암모니아": "아바타로 재생해 보니 손이 머리 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "대기": "아바타로 재생해 보니 한 손이 얼굴을 덮고 다른 손이 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
    "포함": "아바타로 재생해 보니 손이 머리 위로 올라감 (다른 문장에서 잘라 온 교체 자료, 뜻·동작 검수 필요)",
}
MIN_FRAMES = 8          # 앞뒤를 자른 뒤 이보다 짧으면 문장에서 잘라 온 조각으로 보고 가져오지 않음 (0.27초)
MP_SOURCES = ("mediapipe_relative3d_estimate", "supplied_relative3d")      # 어깨가 좁게 잡힌 자료 (코 높이로 크기 맞춤)
REF_NOSE = 0.524                                                             # AI Hub 3D 목→코 높이 중앙값 (어깨너비 단위)


def nfc(s):
    return unicodedata.normalize("NFC", s)


def fit_z(pairs):
    """깊이 배율: (xy 길이², z 차이, 기준 길이) 들에서 뼈 길이가 기준에 가장 가까워지는 값."""
    best, bs = None, 1.0
    for k in range(0, 151):
        sc = k / 100
        err = sum((math.sqrt(xy2 + (sc * dz) ** 2) - r) ** 2 for xy2, dz, r in pairs)
        if best is None or err < best:
            best, bs = err, sc
    return bs


def load(folder, row, L):
    d = json.loads(find(folder, row["file"]).read_text(encoding="utf-8"))
    st = json.loads(find(folder, row["playback_state"]).read_text(encoding="utf-8"))
    lm, ps = d["landmarks"], st["point_status"]
    rest = st.get("hand_rest_weight") or {}
    skip = st.get("wrist_link_skipped_frames") or {}       # 몸·손 손목이 크게 어긋난 프레임 (이상치) → 그 손은 없음
    skip = {k: set(v) for k, v in skip.items()} if isinstance(skip, dict) else {}
    face_ok = set(st.get("face_enabled_frames") or [])
    T = len(lm["pose_keypoints_3d"])

    def pts(key, t, n):
        row_ = lm[key][t] if t < len(lm[key]) else None
        if not row_:
            return None
        s = (ps.get(key) or [[1] * n] * T)[t]
        out = []
        for i in range(n):
            x, y, z = row_[3 * i: 3 * i + 3]
            out.append([x, -y, -z] if s[i] != 0 and (x or y or z) else None)   # 이 저장소 기준으로 y·z 뒤집기
        return out if sum(p is not None for p in out) >= 5 else None

    frames = []
    for t in range(T):
        pose = pts("pose_keypoints_3d", t, 25)
        f = {"body": pose[:8] if pose else None,
             "rh": None if (rest.get("Right") or [0] * T)[t] >= 0.5 or t in skip.get("Right", ()) else pts("hand_right_keypoints_3d", t, 21),
             "lh": None if (rest.get("Left") or [0] * T)[t] >= 0.5 or t in skip.get("Left", ()) else pts("hand_left_keypoints_3d", t, 21),
             "face": pts("face_keypoints_3d", t, 70) if t in face_ok else None}
        if pose and pose[0] and pose[15] and pose[16]:
            f["fk"] = [pose[0], pose[16], pose[15]]          # 코, 왼눈, 오른눈 → 얼굴 근처 손 위치 맞추기 (아바타)
        frames.append(f)

    mode = row["source_mode"]
    # 1) 크기: MediaPipe·후처리본은 어깨가 좁게 잡혀 몸이 크게 나옴 → 목→코 높이를 AI Hub 3D 중앙값에 맞춤 (어깨는 그대로)
    ks = 1.0
    if mode in MP_SOURCES:
        noses = [f["body"][1][1] - f["body"][0][1] for f in frames if f["body"] and f["body"][0] and f["body"][1]]
        if noses:
            ks = min(1.0, max(0.6, REF_NOSE / max(1e-6, sorted(noses)[len(noses) // 2])))
    for f in frames:
        b = f["body"]
        if not (b and b[1]) or ks == 1.0:
            continue
        neck = b[1]
        sc = lambda p: None if p is None else [round(neck[c] + (p[c] - neck[c]) * ks, 4) for c in range(3)]
        f["body"] = [p if i in (2, 5) else sc(p) for i, p in enumerate(b)]
        for k in ("rh", "lh", "fk", "face"):
            if f.get(k):
                f[k] = [sc(p) for p in f[k]]

    # 2) 깊이 배율. AI Hub 3D(여러 카메라)는 그대로. MediaPipe 몸 깊이는 팔 뼈 길이로 맞추고 손가락 깊이(손 3D 모델)는 그대로.
    #    후처리본(avatar_derived)은 몸·손 모두 깊이가 커서 둘 다 맞춤 (load_mediapipe_avatar 와 같음)
    sb, sh = 1.0, {"rh": 1.0, "lh": 1.0}
    if mode in MP_SOURCES:
        arm = [((2, 3), L["arm"][(2, 3)]), ((3, 4), L["arm"][(3, 4)]), ((5, 6), L["arm"][(5, 6)]), ((6, 7), L["arm"][(6, 7)])]
        pairs = []
        for f in frames:
            b = f["body"]
            for (i, j), r in arm:
                if b and b[i] and b[j]:
                    v = [b[j][k] - b[i][k] for k in range(3)]
                    pairs.append((v[0] ** 2 + v[1] ** 2, v[2], r))
        sb = fit_z(pairs) if pairs else 1.0
    if mode == "supplied_relative3d":
        for k in ("rh", "lh"):
            pp = []
            for f in frames:
                h = f[k]
                if not (h and h[0] and h[9]):
                    continue
                palm = math.dist(h[0][:2], h[9][:2])
                for (i, j) in HAND_BONES:
                    if h[i] and h[j]:
                        v = [h[j][c] - h[i][c] for c in range(3)]
                        pp.append((v[0] ** 2 + v[1] ** 2, v[2], L["hand"][(i, j)] / L["hand"][(0, 9)] * palm))
            sh[k] = fit_z(pp) if len(pp) > 20 else sb
    for f in frames:
        b = f["body"]
        if not (b and b[1]) or (sb == 1.0 and sh["rh"] == 1.0 and sh["lh"] == 1.0):
            continue
        nz = b[1][2]
        zb = lambda p: None if p is None else [p[0], p[1], round(nz + (p[2] - nz) * sb, 4)]
        f["body"] = [zb(p) for p in b]
        if f.get("fk"):
            f["fk"] = [zb(p) for p in f["fk"]]
        for k, wi in (("rh", 4), ("lh", 7)):
            h = f[k]
            if not (h and h[0]):
                continue
            w0 = h[0]
            local = [None if p is None else [p[0], p[1], w0[2] + (p[2] - w0[2]) * sh[k]] for p in h]
            anchor = f["body"][wi] or zb(w0)              # 손목을 몸 손목 위치로 (손이 팔에서 떨어지지 않게)
            f[k] = [None if p is None else [round(p[c] - w0[c] + anchor[c], 4) for c in range(3)] for p in local]
    return frames, float(d.get("fps", 30)), dict(sb=sb, sh=sh, ks=ks)


def trim_rise(frames, keep=0.5, pad=3):
    """사전 영상: 앞뒤의 '쉬는 자세에서 손 올리기·내리기' 구간 잘라내기 (trim_rest 는 허리 아래 쉬는 손만 자름).
    가장 높이 든 손의 높이가 처음(끝) 높이에서 가장 높은 곳까지의 keep 만큼 올라온 뒤부터(내려가기 전까지)만 남김.
    처음·끝이 이미 높으면(쉬는 자세가 아니면) 자르지 않음. 손 올리기·내리기는 이어붙일 때 compose 가 따로 만듦."""
    def top(f):
        b = f.get("body")
        ys = [b[w][1] for w in (4, 7) if b and b[w]] if b else []
        return min(ys) if ys else None
    ys = [top(f) for f in frames]
    vals = [y for y in ys if y is not None]
    if not vals:
        return frames
    peak = min(vals)
    first = next(y for y in ys if y is not None)
    last = next(y for y in reversed(ys) if y is not None)
    a, b = 0, len(frames) - 1
    if first - peak > 0.3:
        th = first - keep * (first - peak)
        a = next(i for i, y in enumerate(ys) if y is not None and y <= th)
    if last - peak > 0.3:
        th = last - keep * (last - peak)
        b = max(i for i, y in enumerate(ys) if y is not None and y <= th)
    return frames[max(0, a - pad): min(len(frames), b + pad + 1)]


def find(folder, rel):
    """한글 파일 이름이 NFD(맥)·NFC 어느 쪽이어도 찾기."""
    p = Path(folder)
    for part in Path(rel).parts:
        hit = p / part
        if not hit.exists():
            hit = next((x for x in p.iterdir() if nfc(x.name) == nfc(part)), hit)
        p = hit
    return p


def read_rows(folder):
    """처리현황.csv 또는 자료목록.json → 단어별 행 (word, file, playback_state, source_mode, source_id, service_use_policy, gloss_raw)."""
    csv_p, json_p = find(folder, "처리현황.csv"), find(folder, "자료목록.json")
    if csv_p.exists():
        return list(csv.DictReader(open(csv_p, encoding="utf-8-sig")))
    return [{k: (str(v) if not isinstance(v, str) else v) for k, v in it.items()} for it in json.loads(json_p.read_text(encoding="utf-8"))["items"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default="data/sign/motions")
    ap.add_argument("--only", nargs="*", help="이 단어들만 (폴더의 단어 이름)")
    ap.add_argument("--dry", action="store_true", help="저장하지 않고 보고만")
    a = ap.parse_args()
    folder = Path(a.folder).expanduser()
    md = MotionDict(a.out)
    L = bone_lengths([md.get(g)["frames"] for g in md.index if "2D" not in (md.get(g).get("source") or "")])
    removed = {_strip_num(g) for g in REMOVED}
    rows = read_rows(folder)
    clips, report = {}, []
    for row in rows:
        word = nfc(row["word"])
        if a.only and word not in a.only:
            continue
        g = NAMES.get(word) or nfc(row.get("gloss_raw") or "") or word + "1"
        if word in SKIP and not a.only:
            report.append(f"  {word:<8} 가져오지 않음: {SKIP[word]}")
            continue
        if row["service_use_policy"] != "integration_candidate":
            report.append(f"  {word:<8} 검토용 자료 ({row['service_use_policy']}) → 가져오지 않음")
            continue
        if _strip_num(g) in removed:
            report.append(f"  {word:<8} 검수에서 뺀 단어 → 가져오지 않음")
            continue
        if md.resolve(g):
            report.append(f"  {word:<8} 이미 있음({md.resolve(g)}) → 그대로")
            continue
        frames, fps, info = load(folder, row, L)
        clips[g] = (frames, fps, row, info)
    calibrate_expressions([c[0] for c in clips.values()])
    for g, (frames, fps, row, info) in clips.items():
        fr = normalize(frames)
        n0 = len(fr)
        fr = trim_rest(fr)
        if row["source_mode"] in MP_SOURCES:             # 사전 영상: 앞뒤 손 올리기·내리기도 자름
            fr = trim_rise(fr)
        if len(fr) < MIN_FRAMES:
            report.append(f"  {nfc(row['word']):<8} 가져오지 않음: {len(fr)}프레임 (너무 짧은 조각)")
            continue
        src = (f"팀원 3D처리본 {row['source_mode']} {row['source_id']} ({n0}→{len(fr)}프레임, "
               f"깊이배율 몸 {info['sb']:.2f}·손 {info['sh']['rh']:.2f}/{info['sh']['lh']:.2f}, 크기 {info['ks']:.2f}) (3D)")
        report.append(f"  {nfc(row['word']):<8} → {g:<6} {len(fr):>3}프레임 ({len(fr) / fps:.1f}초)  {row['source_mode']}"
                      f"  깊이 {info['sb']:.2f}/{info['sh']['rh']:.2f}/{info['sh']['lh']:.2f} 크기 {info['ks']:.2f}")
        if not a.dry:
            MotionDict.save_clip(a.out, g, fr, round(fps), source=src)
    print(f"새 동작 {sum(1 for r in report if '→' in r and '프레임 (' in r)}개" + (" (저장 안 함)" if a.dry else f" → {a.out}"))
    print("\n".join(report))


if __name__ == "__main__":
    main()
