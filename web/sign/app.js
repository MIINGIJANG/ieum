// 카메라 수어 입력 (시험판, 본 화면과 따로): 웹캠 → 관절 점(MediaPipe Holistic, 기기 안) → 낱말 인식(ONNX, 기기 안)
// → 후보에서 골라 고치기 → 비슷한 예시 질문 / 낱말로 바로 묻기 → 이음 서버(/query) 답변.
// 인식 모델·특징 변환은 팀원 SignBridge(github.com/BioCode67/signbridge) 것을 그대로 씀:
//   src/recognition/landmarks.ts(특징 155차원) · onnxRecognizer.ts · useRecognizer.ts(확정 규칙) · holistic.ts
// 영상은 기기 밖으로 나가지 않음 (서버에는 고른 낱말·질문 글자만 감).
import { FilesetResolver, HolisticLandmarker } from "./lib/mediapipe/vision_bundle.mjs";
import * as ort from "./lib/ort/ort.wasm.min.mjs";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const API = new URL("../", location.href).href;          // 이음 서버 (같은 주소의 위 폴더)

// ---------------- 특징 (팀원 landmarks.ts 를 그대로 옮김 — 바꾸면 모델과 어긋남) ----------------
const POSE_KEYS = [0, 11, 12, 13, 14, 15, 16, 23, 24];   // 코·양 어깨·팔꿈치·손목·엉덩이
const FEATURE_DIM = POSE_KEYS.length * 3 + 21 * 3 * 2 + 2; // 155
const SEQ_LEN = 32;

function frameToFeatures(frame) {
  const pose = frame.pose;
  if (pose.length < 25) return null;
  const lS = pose[11], rS = pose[12];
  if (!lS || !rS) return null;
  const cx = (lS.x + rS.x) / 2, cy = (lS.y + rS.y) / 2, cz = (lS.z + rS.z) / 2;
  const sw = Math.hypot(lS.x - rS.x, lS.y - rS.y);
  if (sw < 1e-4) return null;
  const inv = 1 / sw, out = new Float32Array(FEATURE_DIM);
  let o = 0;
  const push = (p) => { out[o++] = (p.x - cx) * inv; out[o++] = (p.y - cy) * inv; out[o++] = (p.z - cz) * inv; };
  for (const i of POSE_KEYS) { if (pose[i]) push(pose[i]); else o += 3; }   // 안 잡힌 점은 0
  const hasL = frame.leftHand.length === 21, hasR = frame.rightHand.length === 21;
  if (hasL) for (let i = 0; i < 21; i++) push(frame.leftHand[i]); else o += 63;
  if (hasR) for (let i = 0; i < 21; i++) push(frame.rightHand[i]); else o += 63;
  out[o++] = hasL ? 1 : 0; out[o++] = hasR ? 1 : 0;
  return out;
}
function resampleSequence(frames, seqLen = SEQ_LEN) {
  const out = new Float32Array(seqLen * FEATURE_DIM);
  if (!frames.length) return out;
  for (let t = 0; t < seqLen; t++) {
    const src = frames.length === 1 ? 0 : Math.round((t * (frames.length - 1)) / (seqLen - 1));
    out.set(frames[src], t * FEATURE_DIM);
  }
  return out;
}

// ---------------- 인식 규칙 (팀원 useRecognizer.ts 와 같은 값) ----------------
const BUFFER_CAP = 48, INFER_MS = 250, MIN_FRAMES = 12, HAND_MIN_RATIO = 0.25, STABLE_HITS = 2, CONF = 0.6;

let landmarker = null, session = null, inputName = "input", labels = [], domain = null, domainIdx = null;
let stream = null, raf = 0, lastTs = -1, buf = [], inFlight = false, timer = 0;
let pending = { label: "", hits: 0 }, lastCommitted = "";
const words = [];                                         // [{label, alts:[...]}]
const fpsTimes = [];

// ---------------- 불러오기 ----------------
// 산업안전 단어 목록·문서 말 바꾸기는 카메라를 켜기 전에도 씀 (낱말 고치기·질문 찾기)
const domainReady = fetch("./domain.json").then((r) => r.json()).then((d) => { domain = d; renderWords(); }).catch(() => null);
async function fetchWithProgress(url, onP) {
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`${url} (${res.status})`);
  const total = Number(res.headers.get("content-length") || 0), reader = res.body.getReader(), chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length; onP && onP(got, total);
  }
  const out = new Uint8Array(got);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}
function setLoad(text, pct) { $("loadMsg").textContent = text; if (pct != null) $("loadBar").style.width = `${pct}%`; }

async function loadAll() {
  setLoad("단어 목록 불러오는 중…", 2);
  const meta = await (await fetch("./models/meta.json")).json();
  if (meta.feature_dim !== FEATURE_DIM || meta.seq_len !== SEQ_LEN)
    throw new Error(`특징 규격이 다름: 모델 ${meta.feature_dim}×${meta.seq_len}, 화면 ${FEATURE_DIM}×${SEQ_LEN}`);
  labels = meta.labels;
  await domainReady;
  if (domain) { const set = new Set(domain.labels); domainIdx = labels.map((l, i) => set.has(l) ? i : -1).filter((i) => i >= 0); }
  $("vocabInfo").textContent = `전체 ${meta.num_classes.toLocaleString()}개 · 산업안전 ${domainIdx ? domainIdx.length : 0}개 (검증 정확도 ${(meta.val_top1 * 100).toFixed(1)}%, 전체 단어 기준)`;

  setLoad("관절 인식 모델 불러오는 중…", 5);
  const vision = await FilesetResolver.forVisionTasks(new URL("./lib/mediapipe", location.href).href);
  const taskBytes = await fetchWithProgress("./models/holistic_landmarker.task", (g, t) => t && setLoad("관절 인식 모델 불러오는 중…", 5 + 35 * g / t));
  for (const delegate of ["GPU", "CPU"]) {
    try {
      landmarker = await HolisticLandmarker.createFromOptions(vision, { baseOptions: { modelAssetBuffer: taskBytes, delegate }, runningMode: "VIDEO" });
      break;
    } catch (e) { console.warn("Holistic", delegate, "실패", e); }
  }
  if (!landmarker) throw new Error("관절 인식 모델을 시작하지 못함");

  setLoad("수어 단어 인식 모델 불러오는 중…", 40);
  ort.env.wasm.wasmPaths = new URL("./lib/ort/", location.href).href;
  ort.env.wasm.numThreads = 1;                           // 크로스오리진 격리 없이도 동작하게
  const model = await fetchWithProgress("./models/model.onnx", (g, t) => t && setLoad("수어 단어 인식 모델 불러오는 중…", 40 + 55 * g / t));
  session = await ort.InferenceSession.create(model, { executionProviders: ["wasm"], graphOptimizationLevel: "all" });
  inputName = session.inputNames[0];
  await session.run({ [inputName]: new ort.Tensor("float32", new Float32Array(SEQ_LEN * FEATURE_DIM), [1, SEQ_LEN, FEATURE_DIM]) });   // 첫 추론 미리
  setLoad("준비 완료", 100);
}

// ---------------- 인식 ----------------
async function predict(seq) {
  const out = await session.run({ [inputName]: new ort.Tensor("float32", seq, [1, SEQ_LEN, FEATURE_DIM]) });
  const logits = out[session.outputNames[0]].data;
  const idx = $("domainOnly").checked && domainIdx && domainIdx.length ? domainIdx : null;   // 산업안전 단어만이면 그 안에서만 softmax
  const ids = idx || [...logits.keys()];
  let max = -Infinity;
  for (const i of ids) if (logits[i] > max) max = logits[i];
  let sum = 0;
  const ex = new Map();
  for (const i of ids) { const e = Math.exp(logits[i] - max); ex.set(i, e); sum += e; }
  const top = [...ex.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([i, e]) => ({ label: labels[i], p: e / sum }));
  return top;
}
function word(label) { return (domain && domain.words[label]) || label.replace(/[\d#]+$/, ""); }

function tick() {
  if (buf.length < MIN_FRAMES || !session) return;
  const withHand = buf.filter((f) => f[FEATURE_DIM - 2] > 0 || f[FEATURE_DIM - 1] > 0).length;
  const ok = withHand / buf.length >= HAND_MIN_RATIO;
  $("handHint").hidden = ok;
  if (!ok) { pending = { label: "", hits: 0 }; $("live").textContent = "—"; return; }   // 손이 없으면 답을 내지 않음
  if (inFlight) return;
  inFlight = true;
  predict(resampleSequence(buf)).then((top) => {
    $("live").innerHTML = top.slice(0, 3).map((c) => `${esc(word(c.label))} <small>${Math.round(c.p * 100)}%</small>`).join(" · ");
    const best = top[0];
    if (best.p >= CONF) {
      if (pending.label === best.label) pending.hits++; else pending = { label: best.label, hits: 1 };
      if (pending.hits >= STABLE_HITS && best.label !== lastCommitted) {
        lastCommitted = best.label;
        words.push({ label: best.label, alts: top.map((c) => c.label) });
        renderWords();
      }
    } else pending = { label: "", hits: 0 };
  }).catch((e) => console.error(e)).finally(() => { inFlight = false; });
}

function loop() {
  raf = requestAnimationFrame(loop);
  const v = $("video");
  if (v.readyState < 2) return;
  const now = performance.now();
  if (now <= lastTs) return;
  lastTs = now;
  let res;
  try { res = landmarker.detectForVideo(v, now); } catch { return; }
  const pick = (a) => (a && a.length ? a[0].map((p) => ({ x: p.x, y: p.y, z: p.z })) : []);
  const frame = { pose: pick(res.poseLandmarks), leftHand: pick(res.leftHandLandmarks), rightHand: pick(res.rightHandLandmarks) };
  draw(frame, v.videoWidth, v.videoHeight);
  const f = frameToFeatures(frame);
  if (f) { buf.push(f); if (buf.length > BUFFER_CAP) buf.shift(); }
  fpsTimes.push(now); while (fpsTimes.length && now - fpsTimes[0] > 1000) fpsTimes.shift();
  $("fps").textContent = `${fpsTimes.length}fps · 몸 ${frame.pose.length ? "✓" : "✗"} · 왼손 ${frame.leftHand.length ? "✓" : "✗"} · 오른손 ${frame.rightHand.length ? "✓" : "✗"}`;
}

function draw(frame, w, h) {
  const cv = $("overlay");
  if (cv.width !== w) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  const dot = (p, r, c) => { g.fillStyle = c; g.beginPath(); g.arc(p.x * w, p.y * h, r, 0, 7); g.fill(); };
  for (const i of POSE_KEYS) if (frame.pose[i]) dot(frame.pose[i], 4, "#f39a45");
  for (const p of frame.leftHand) dot(p, 3, "#4aa3ff");
  for (const p of frame.rightHand) dot(p, 3, "#44d17a");
}

async function startCamera() {
  $("start").disabled = true;
  try {
    if (!session) await loadAll();
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
    const v = $("video");
    v.srcObject = stream;
    await v.play();
    $("loadBox").hidden = true; $("run").hidden = false;
    buf = []; raf = requestAnimationFrame(loop);
    timer = setInterval(tick, INFER_MS);
  } catch (e) {
    console.error(e);
    setLoad(`시작하지 못했습니다: ${e.message || e}`, null);
    $("start").disabled = false;
  }
}
function stopCamera() {
  cancelAnimationFrame(raf); clearInterval(timer);
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null; $("video").srcObject = null;
  $("run").hidden = true; $("loadBox").hidden = false; $("start").disabled = false; $("start").textContent = "카메라 다시 켜기";
}

// ---------------- 낱말 · 질문 ----------------
function renderWords() {
  $("words").innerHTML = words.map((w, i) => `<span class="chip"><button class="w" data-i="${i}">${esc(word(w.label))}</button><button class="x" data-x="${i}" aria-label="지우기">✕</button></span>`).join("") || `<span class="empty">수어를 하면 여기에 낱말이 쌓입니다</span>`;
  $("alts").hidden = true;
  suggest();
}
$("words").onclick = (e) => {
  const x = e.target.closest("[data-x]"), w = e.target.closest("[data-i]");
  if (x) { words.splice(+x.dataset.x, 1); lastCommitted = ""; renderWords(); return; }
  if (w) {
    const i = +w.dataset.i;
    $("alts").hidden = false;
    $("alts").innerHTML = `<span>다른 후보:</span>` + words[i].alts.map((l) => `<button data-r="${i}" data-l="${esc(l)}">${esc(word(l))}</button>`).join("");
  }
};
$("alts").onclick = (e) => {
  const b = e.target.closest("[data-r]");
  if (!b) return;
  words[+b.dataset.r].label = b.dataset.l;
  renderWords();
};
$("clear").onclick = () => { words.length = 0; lastCommitted = ""; renderWords(); $("answer").hidden = true; };

let examples = [];
// 질문 찾기 후보: 서버의 예시 질문 + domain.json 의 질문들 (모범 답안이 있는 질문·개발용 평가 질문)
fetch(API + "examples").then((r) => r.json()).then((x) => { examples = [...new Set([...x.map((e) => e.question), ...examples])]; suggest(); }).catch(() => {});
domainReady.then(() => { examples = [...new Set([...examples, ...((domain && domain.questions) || [])])]; suggest(); });
const bigrams = (s) => { s = s.replace(/\s/g, ""); const out = new Set(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); return out; };
// 수어 낱말 → 문서에 쓰는 말: 대체 표현을 거꾸로(머리 + 보호 → 안전모), 단어표를 거꾸로(입다 → 착용). 원래 말도 같이 둠
function docWords(ws) {
  let out = [...ws];
  for (const c of (domain && domain.combos) || []) {
    const at = c.words.map((w) => out.indexOf(w));
    if (at.every((i) => i >= 0)) { out = out.filter((w) => !c.words.includes(w)); out.splice(Math.min(...at), 0, c.as); }
  }
  const map = (domain && domain.to_doc) || {};
  return [...new Set(out.flatMap((w) => (map[w] ? [map[w], w] : [w])))];
}
function suggest() {
  const ws = docWords(words.map((w) => word(w.label)));
  const box = $("suggest");
  if (!ws.length) { box.innerHTML = ""; return; }
  // 낱말이 질문에 얼마나 들어 있는지 (말 그대로 1, 두 글자 묶음만 겹치면 0.5).
  // 드문 말일수록 무겁게 ('언제'처럼 여러 질문에 있는 말보다 '안전모'가 질문을 더 잘 가림)
  const hit = (q, w) => (q.includes(w) ? 1 : [...bigrams(w)].some((b) => bigrams(q).has(b)) ? 0.5 : 0);
  const weight = Object.fromEntries(ws.map((w) => [w, Math.log((examples.length + 1) / (1 + examples.filter((q) => q.includes(w)).length)) + 0.1]));
  const total = ws.reduce((a, w) => a + weight[w], 0) || 1;
  const scored = examples.map((q) => [ws.reduce((a, w) => a + weight[w] * hit(q, w), 0) / total, q])
    .filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, 3);
  box.innerHTML = scored.map(([s, q]) => `<button class="q" data-q="${esc(q)}">${esc(q)}</button>`).join("")
    + `<button class="q direct" data-q="${esc(ws.join(" "))}">낱말로 바로 묻기: “${esc(ws.join(" "))}”</button>`;
}
$("suggest").onclick = async (e) => {
  const b = e.target.closest("[data-q]");
  if (!b) return;
  const q = b.dataset.q;
  const a = $("answer");
  a.hidden = false; a.innerHTML = `<p>“${esc(q)}” 답을 찾는 중…</p>`;
  try {
    const d = await (await fetch(API + "query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: q }) })).json();
    const blocks = (d.sentences || []).flatMap((s) => (s.words || []).filter(Boolean));
    a.innerHTML = `<p class="q">질문: ${esc(q)}</p><p>${esc(d.answer_text || "관련 내용을 찾지 못했습니다.")}</p>`
      + (blocks.length ? `<p class="blocks">수어 블록: ${blocks.map(esc).join(" · ")}</p>` : "");
  } catch (err) {
    a.innerHTML = `<p>서버에 묻지 못했습니다.</p>`;
  }
};

$("start").onclick = startCamera;
$("stop").onclick = stopCamera;
renderWords();
// 시험용: 카메라 없이 특징 함수·모델을 확인할 수 있게
window.__sign = { frameToFeatures, resampleSequence, predict, loadAll, FEATURE_DIM, SEQ_LEN, words, renderWords, docWords };
