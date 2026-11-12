// 관절 점 엔진 (화면·작업자 스레드 어디서나): 영상 한 장 → {pose, leftHand, rightHand}
//  - "holistic": MediaPipe Holistic (팀원 SignBridge 와 같음, 얼굴 점 468개까지 계산해서 무거움)
//  - "light":   몸(Pose lite) + 손(Hand) 따로 — 인식에 쓰는 몸 9점·손만 계산해서 가벼움.
//               왼손·오른손은 '몸 관절의 왼/오른 손목에 가까운 손'으로 정함 (Holistic 과 같은 기준: 수어자 본인의 왼손·오른손)
import { FilesetResolver, HandLandmarker, HolisticLandmarker, PoseLandmarker } from "./lib/mediapipe/vision_bundle.mjs";

const pts = (a) => (a ? a.map((p) => ({ x: p.x, y: p.y, z: p.z, visibility: p.visibility })) : []);

async function bytes(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url} (${r.status})`); return new Uint8Array(await r.arrayBuffer()); }

export async function createEngine(kind, base) {
  // 라이브러리(wasm)와 모델 파일을 동시에 받음 (차례로 받으면 그만큼 첫 로딩이 길어짐). 엔진 만들기는 차례로
  const files = kind === "light" ? ["pose_landmarker_lite.task", "hand_landmarker.task"] : ["holistic_landmarker.task"];
  const [vision, ...bufs] = await Promise.all([FilesetResolver.forVisionTasks(new URL(base + "lib/mediapipe", self.location.href).href),
    ...files.map((f) => bytes(base + "models/" + f))]);
  const make = async (Cls, file, extra = {}) => {
    const buf = bufs[files.indexOf(file)];
    for (const delegate of ["GPU", "CPU"]) {
      try { return await Cls.createFromOptions(vision, { baseOptions: { modelAssetBuffer: buf, delegate }, runningMode: "VIDEO", ...extra }); }
      catch (e) { console.warn(file, delegate, "실패", e); }
    }
    throw new Error(`${file} 를 시작하지 못함`);
  };
  if (kind === "light") {
    const pose = await make(PoseLandmarker, "pose_landmarker_lite.task", { numPoses: 1 });
    const hand = await make(HandLandmarker, "hand_landmarker.task", { numHands: 2 });
    return {
      kind,
      detect(img, ts) {
        const pr = pose.detectForVideo(img, ts), hr = hand.detectForVideo(img, ts);
        const p = pts(pr.landmarks && pr.landmarks[0]);
        const out = { pose: p, leftHand: [], rightHand: [] };
        const hands = (hr.landmarks || []).map(pts);
        if (!hands.length) return out;
        if (p.length >= 17) {                          // 몸의 왼손목(15)·오른손목(16)에 가까운 손
          const d = (h, w) => Math.hypot(h[0].x - w.x, h[0].y - w.y);
          const L = p[15], R = p[16];
          if (hands.length === 1) { const h = hands[0]; out[d(h, L) <= d(h, R) ? "leftHand" : "rightHand"] = h; }
          else {
            const [a, b] = hands;
            if (d(a, L) + d(b, R) <= d(a, R) + d(b, L)) { out.leftHand = a; out.rightHand = b; } else { out.leftHand = b; out.rightHand = a; }
          }
        }
        return out;
      },
      close() { pose.close(); hand.close(); },
    };
  }
  const holo = await make(HolisticLandmarker, "holistic_landmarker.task");
  return {
    kind,
    detect(img, ts) {
      const r = holo.detectForVideo(img, ts), first = (a) => (a && a.length ? a[0] : null);
      return { pose: pts(first(r.poseLandmarks)), leftHand: pts(first(r.leftHandLandmarks)), rightHand: pts(first(r.rightHandLandmarks)) };
    },
    close() { holo.close(); },
  };
}
