// 관절 점 계산을 화면과 다른 스레드에서 (화면이 버벅이지 않고, 카메라 프레임을 덜 놓침)
import { createEngine } from "./engines.js";

// MediaPipe 는 작업자 스레드에서 importScripts 로 wasm 로더를 읽는데, 모듈 작업자에서는 importScripts 를 못 씀
// → 같은 일을 하는 함수로 바꿔 둠 (동기 요청으로 받아 전역에서 실행)
self.importScripts = (...urls) => {
  for (const u of urls) {
    const x = new XMLHttpRequest();
    x.open("GET", u, false);
    x.send();
    if (x.status >= 400) throw new Error(`${u} (${x.status})`);
    (0, eval)(x.responseText);
  }
};

let engine = null;
self.onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.type === "init") {
      if (engine) engine.close();
      engine = await createEngine(m.kind, m.base);
      self.postMessage({ type: "ready", kind: engine.kind });
    } else if (m.type === "frame" && engine) {
      const frame = engine.detect(m.bitmap, m.ts);
      m.bitmap.close();
      self.postMessage({ type: "frame", ts: m.ts, frame });
    }
  } catch (err) {
    if (m.bitmap) m.bitmap.close();
    self.postMessage({ type: "error", message: String(err && err.message || err), during: m.type });
  }
};
