"""영상(한국수어사전 등) → MediaPipe 관절 좌표 → 동작사전에 저장.

python scripts/data/extract_keypoints.py --video 보안경.mp4 --gloss 보안경 --out data/sign/motions
python scripts/data/extract_keypoints.py --video_dir 사전영상/ --out data/sign/motions     # 파일명 = 글로스

- 처음 실행 때 MediaPipe 모델 파일 2개를 models/ 에 자동으로 받음 (인터넷 필요)
- 앞뒤 손 내린 구간은 자동으로 잘라냄 (trim_idle)
- pip install mediapipe opencv-python
"""
import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.motion import MotionDict, mediapipe_frame, normalize, trim_idle  # noqa: E402

MODELS = {
    "hand": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "pose": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
}
MODEL_DIR = Path(__file__).resolve().parents[2] / "models"


def ensure_models():
    MODEL_DIR.mkdir(exist_ok=True)
    paths = {}
    for k, url in MODELS.items():
        p = MODEL_DIR / url.rsplit("/", 1)[-1]
        if not p.exists():
            print(f"[model] {p.name} 다운로드 중...")
            urllib.request.urlretrieve(url, p)
        paths[k] = str(p)
    return paths


def extract(video, paths, fps_out=30):
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    hand = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=paths["hand"]),
        running_mode=vision.RunningMode.VIDEO, num_hands=2))
    pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=paths["pose"]),
        running_mode=vision.RunningMode.VIDEO))

    cap = cv2.VideoCapture(str(video))
    fps_in = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1.0, fps_in / fps_out)
    frames, i, nxt = [], 0, 0.0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if i >= nxt:
            nxt += step
            h, w = img.shape[:2]
            rgb = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            ts = int(i * 1000 / fps_in)
            pr = pose.detect_for_video(rgb, ts)
            hr = hand.detect_for_video(rgb, ts)
            frames.append(mediapipe_frame(pr.pose_landmarks[0] if pr.pose_landmarks else None,
                                          hr.hand_landmarks, w, h))
        i += 1
    cap.release()
    hand.close()
    pose.close()
    return frames, min(fps_out, fps_in)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--gloss", help="--video 와 함께 사용. 생략하면 파일명")
    ap.add_argument("--video_dir", help="폴더 안 영상 전부, 파일명이 글로스")
    ap.add_argument("--out", default="data/sign/motions")
    a = ap.parse_args()
    vids = [(Path(a.video), a.gloss or Path(a.video).stem)] if a.video else \
           [(p, p.stem) for p in sorted(Path(a.video_dir).iterdir())
            if p.suffix.lower() in (".mp4", ".mov", ".avi", ".webm", ".mkv")]
    paths = ensure_models()
    for v, g in vids:
        frames, fps = extract(v, paths)
        clip = normalize(trim_idle(frames))
        seen = sum(1 for f in clip if f["lh"] or f["rh"])
        MotionDict.save_clip(a.out, g, clip, round(fps), source=f"video:{v.name}")
        print(f"{g}: {len(clip)} 프레임 (손 인식 {seen}) → {a.out}")


if __name__ == "__main__":
    main()
