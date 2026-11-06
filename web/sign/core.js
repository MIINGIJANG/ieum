// 카메라 수어 입력 공통 부분 (화면 app.js 와 평가 eval.js 가 같이 씀).
//   관절 점 → (떨림 줄이기·손 끊김 메우기) → 특징 → (손 움직임으로 낱말 끊기) → 인식 → (확신 판단) → (질문 흐름으로 후보 다시 고르기)
// 인식 모델·특징 정의는 팀원 SignBridge(github.com/BioCode67/signbridge) 것 (landmarks.ts · onnxRecognizer.ts · useRecognizer.ts).
import * as ort from "./lib/ort/ort.wasm.min.mjs";

// ---------------- 특징 (팀원 landmarks.ts 그대로 — 바꾸면 모델과 어긋남) ----------------
export const POSE_KEYS = [0, 11, 12, 13, 14, 15, 16, 23, 24];   // 코·양 어깨·팔꿈치·손목·엉덩이
export const FEATURE_DIM = POSE_KEYS.length * 3 + 21 * 3 * 2 + 2; // 155
export const SEQ_LEN = 32;

/** frame = {pose:[{x,y,z}], leftHand:[...21], rightHand:[...21]} (이미지 정규화 좌표). useZ=false 면 깊이를 0 으로 */
export function frameToFeatures(frame, useZ = true) {
  const pose = frame.pose;
  if (pose.length < 25) return null;
  const lS = pose[11], rS = pose[12];
  if (!lS || !rS) return null;
  const cx = (lS.x + rS.x) / 2, cy = (lS.y + rS.y) / 2, cz = (lS.z + rS.z) / 2;
  const sw = Math.hypot(lS.x - rS.x, lS.y - rS.y);
  if (sw < 1e-4) return null;
  const inv = 1 / sw, out = new Float32Array(FEATURE_DIM);
  let o = 0;
  const push = (p) => { out[o++] = (p.x - cx) * inv; out[o++] = (p.y - cy) * inv; out[o++] = useZ ? (p.z - cz) * inv : 0; };
  for (const i of POSE_KEYS) { if (pose[i]) push(pose[i]); else o += 3; }          // 안 잡힌 점은 0
  const hasL = frame.leftHand.length === 21, hasR = frame.rightHand.length === 21;
  if (hasL) for (let i = 0; i < 21; i++) push(frame.leftHand[i]); else o += 63;
  if (hasR) for (let i = 0; i < 21; i++) push(frame.rightHand[i]); else o += 63;
  out[o++] = hasL ? 1 : 0; out[o++] = hasR ? 1 : 0;
  return out;
}
export function resampleSequence(frames, seqLen = SEQ_LEN) {
  const out = new Float32Array(seqLen * FEATURE_DIM);
  if (!frames.length) return out;
  for (let t = 0; t < seqLen; t++) {
    const src = frames.length === 1 ? 0 : Math.round((t * (frames.length - 1)) / (seqLen - 1));
    out.set(frames[src], t * FEATURE_DIM);
  }
  return out;
}

// ---------------- 떨림 줄이기 (One Euro) · 손 끊김 메우기 ----------------
class OneEuro {
  constructor(minCutoff = 1.5, beta = 0.6, dCutoff = 1.0) { Object.assign(this, { minCutoff, beta, dCutoff, x: null, dx: 0, t: null }); }
  static a(cutoff, dt) { const r = 2 * Math.PI * cutoff * dt; return r / (r + 1); }
  f(v, t) {
    if (this.x === null) { this.x = v; this.t = t; return v; }
    const dt = Math.max(1e-3, (t - this.t) / 1000);
    this.t = t;
    const dx = (v - this.x) / dt;
    this.dx += OneEuro.a(this.dCutoff, dt) * (dx - this.dx);
    this.x += OneEuro.a(this.minCutoff + this.beta * Math.abs(this.dx), dt) * (v - this.x);
    return this.x;
  }
}
/** 관절 점 정리: 손이 150ms 안에 다시 잡히면 그 사이는 마지막 손으로 메우고, 좌표 떨림은 One Euro 로 줄임 */
export class FrameCleaner {
  constructor({ smooth = true, fillMs = 150 } = {}) { this.smooth = smooth; this.fillMs = fillMs; this.f = new Map(); this.last = { leftHand: null, rightHand: null }; }
  reset() { this.f.clear(); this.last = { leftHand: null, rightHand: null }; }
  _pts(key, arr, t) {
    if (!this.smooth) return arr;
    return arr.map((p, i) => {
      const k = key + i;
      if (!this.f.has(k)) this.f.set(k, [new OneEuro(), new OneEuro(), new OneEuro()]);
      const [fx, fy, fz] = this.f.get(k);
      return { x: fx.f(p.x, t), y: fy.f(p.y, t), z: fz.f(p.z, t), visibility: p.visibility };
    });
  }
  clean(frame, t) {
    const out = { pose: this._pts("p", frame.pose, t), leftHand: [], rightHand: [] };
    for (const side of ["leftHand", "rightHand"]) {
      if (frame[side].length === 21) { out[side] = this._pts(side[0], frame[side], t); this.last[side] = { t, pts: out[side] }; }
      else if (this.fillMs && this.last[side] && t - this.last[side].t <= this.fillMs) out[side] = this.last[side].pts;
      else { for (let i = 0; i < 21; i++) this.f.delete(side[0] + i); this.last[side] = null; }
    }
    return out;
  }
}

// ---------------- 손 움직임으로 낱말 끊기 ----------------
// 낱말 하나짜리 영상으로 학습한 모델이라, 손을 올린 순간부터 내리거나 잠깐 멈출 때까지를 한 낱말로 잘라 한 번 인식.
// (예전: 최근 48프레임을 계속 봄 → 느린 기기에선 10초어치가 섞임)
export const SEG = { endIdleMs: 250, pauseMs: 350, pauseSpeed: 0.35, moveSpeed: 0.6, minMs: 300, maxMs: 3000, restFrac: 0.8 };
export class Segmenter {
  constructor(opts = {}) { this.o = { ...SEG, ...opts }; this.reset(); }
  reset() { this.cur = null; this.idleSince = null; this.slowSince = null; this.prev = null; this.waitMove = false; }
  /** 손을 들고 있는지: 손이 보이고, 손목이 쉬는 높이(어깨~엉덩이의 restFrac)보다 위. 엉덩이가 안 보이면 손이 보이기만 하면 */
  active(frame) {
    const p = frame.pose, hands = [frame.leftHand, frame.rightHand].filter((h) => h.length === 21);
    if (!hands.length || p.length < 25) return false;
    const sy = (p[11].y + p[12].y) / 2, hipsOk = (p[23].visibility ?? 1) > 0.5 && (p[24].visibility ?? 1) > 0.5;
    if (!hipsOk) return true;
    const rest = sy + this.o.restFrac * ((p[23].y + p[24].y) / 2 - sy);
    return hands.some((h) => h[0].y < rest);
  }
  /** 손목 속도 (어깨너비/초) */
  speed(frame, t) {
    const p = frame.pose;
    if (p.length < 25) return 0;
    const sw = Math.hypot(p[11].x - p[12].x, p[11].y - p[12].y) || 1;
    const w = [frame.leftHand[0] || null, frame.rightHand[0] || null];
    let v = 0;
    if (this.prev && t > this.prev.t) for (let i = 0; i < 2; i++) if (w[i] && this.prev.w[i])
      v = Math.max(v, Math.hypot(w[i].x - this.prev.w[i].x, w[i].y - this.prev.w[i].y) / sw / ((t - this.prev.t) / 1000));
    this.prev = { t, w };
    return v;
  }
  /** 프레임 하나 넣기 → 낱말 하나가 끝났으면 {frames:[특징...], t0, t1} 반환.
   *  - 손을 들면 시작, 손을 내리거나 안 보인 지 endIdleMs 지나면 끝
   *  - 움직인 뒤 pauseMs 동안 거의 멈추면 끝 (손을 든 채 다음 낱말로) → 다시 움직여야 다음 낱말 시작
   *  - maxMs 넘으면 자름, minMs 보다 짧거나 전혀 안 움직인 짧은(0.6초 미만) 구간은 버림 */
  push(frame, feat, t) {
    const o = this.o, act = this.active(frame), v = this.speed(frame, t);
    const close = () => {
      const c = this.cur;
      this.cur = null; this.slowSince = null;
      if (!c || c.t1 - c.t0 < o.minMs || c.frames.length < 4 || (!c.moved && c.t1 - c.t0 < 600)) return null;
      return c;
    };
    if (!act || !feat) {
      this.waitMove = false;
      if (!this.cur) return null;
      this.idleSince ??= t;
      return t - this.idleSince >= o.endIdleMs ? close() : null;
    }
    this.idleSince = null;
    if (!this.cur) {
      if (this.waitMove && v < o.moveSpeed * 0.5) return null;
      this.waitMove = false;
      this.cur = { frames: [], t0: t, t1: t, moved: false };
    }
    const c = this.cur;
    c.frames.push(feat); c.t1 = t;
    if (v >= o.moveSpeed) c.moved = true;
    if (c.moved && v < o.pauseSpeed) {
      this.slowSince ??= t;
      if (t - this.slowSince >= o.pauseMs && t - c.t0 >= o.minMs) { this.waitMove = true; return close(); }
    } else this.slowSince = null;
    if (t - c.t0 >= o.maxMs) { this.waitMove = true; return close(); }
    return null;
  }
}

// ---------------- 인식 ----------------
export class Recognizer {
  async load(base, onProgress) {
    const meta = await (await fetch(base + "models/meta.json")).json();
    if (meta.feature_dim !== FEATURE_DIM || meta.seq_len !== SEQ_LEN) throw new Error(`특징 규격이 다름: 모델 ${meta.feature_dim}×${meta.seq_len}`);
    this.meta = meta; this.labels = meta.labels;
    ort.env.wasm.wasmPaths = new URL(base + "lib/ort/", location.href).href;
    ort.env.wasm.numThreads = 1;                           // 크로스오리진 격리 없이도 동작하게
    const bytes = await fetchBytes(base + "models/model.onnx", onProgress);
    this.session = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"], graphOptimizationLevel: "all" });
    this.input = this.session.inputNames[0];
    await this.logits(new Float32Array(SEQ_LEN * FEATURE_DIM));   // 첫 추론 미리
  }
  async logits(seq) {
    const out = await this.session.run({ [this.input]: new ort.Tensor("float32", seq, [1, SEQ_LEN, FEATURE_DIM]) });
    return out[this.session.outputNames[0]].data;
  }
}
export async function fetchBytes(url, onP) {
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`${url} (${res.status})`);
  const total = Number(res.headers.get("content-length") || 0), reader = res.body.getReader(), chunks = [];
  let got = 0;
  for (;;) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); got += value.length; onP && onP(got, total); }
  const out = new Uint8Array(got);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

/** 로짓 → 후보. 확률은 **전체 단어 기준**으로 계산하고(산업안전 단어만이어도 확률을 부풀리지 않음), 순위만 허용된 단어 안에서.
 *  confident: 1위 확률이 minP 이상이고 2위와 차이가 minMargin 이상 (calibrate=false 면 예전처럼 허용된 단어 안에서 다시 나눈 확률 ≥ 0.6) */
export function candidates(logits, labels, { allowed = null, calibrate = true, minP = 0.25, minMargin = 0.08, k = 5 } = {}) {
  let max = -Infinity;
  for (let i = 0; i < logits.length; i++) if (logits[i] > max) max = logits[i];
  const p = new Float32Array(logits.length);
  let sum = 0;
  for (let i = 0; i < logits.length; i++) { p[i] = Math.exp(logits[i] - max); sum += p[i]; }
  for (let i = 0; i < p.length; i++) p[i] /= sum;
  const ids = allowed || [...p.keys()];
  const top = [...ids].sort((a, b) => p[b] - p[a]).slice(0, k);
  let cands, confident;
  if (calibrate) {
    cands = top.map((i) => ({ label: labels[i], p: p[i] }));
    confident = cands.length > 0 && cands[0].p >= minP && cands[0].p - (cands[1]?.p ?? 0) >= minMargin;
  } else {
    const s = ids.reduce((a, i) => a + p[i], 0) || 1;           // 예전 방식: 허용된 단어 안에서 다시 나눔
    cands = top.map((i) => ({ label: labels[i], p: p[i] / s }));
    confident = cands.length > 0 && cands[0].p >= 0.6;
  }
  return { cands, confident };
}

// ---------------- 새 낱말 (재학습 없이: 녹화해 둔 예시와 가장 비슷하면) ----------------
// 예시의 '모양'은 인식 모델의 로짓 전체(13,576개)를 표준화한 것 → 모델이 아는 수어들과 얼마나 닮았는지로 비교
export function embed(logits) {
  let m = 0, s = 0;
  for (const v of logits) m += v;
  m /= logits.length;
  for (const v of logits) s += (v - m) ** 2;
  s = Math.sqrt(s / logits.length) || 1;
  const q = new Int8Array(logits.length);
  for (let i = 0; i < logits.length; i++) q[i] = Math.max(-127, Math.min(127, Math.round(((logits[i] - m) / s) * 30)));
  return q;
}
function cos(a, b) { let d = 0, na = 0, nb = 0; for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; } return d / (Math.sqrt(na * nb) || 1); }
export const toB64 = (q) => btoa(String.fromCharCode(...new Uint8Array(q.buffer)));
export const fromB64 = (s) => new Int8Array(Uint8Array.from(atob(s), (c) => c.charCodeAt(0)).buffer);
/** protos = [{word, samples:[Int8Array]}] → 가장 비슷한 새 낱말 {word, sim} */
export function matchCustom(logits, protos) {
  if (!protos || !protos.length) return null;
  const e = embed(logits);
  let best = null;
  for (const pr of protos) for (const s of pr.samples) { const c = cos(e, s); if (!best || c > best.sim) best = { word: pr.word, sim: c }; }
  return best;
}

// ---------------- 낱말 → 문서 말 · 질문 흐름 ----------------
export const base = (g) => String(g).replace(/[\d#]+$/, "");
/** 대체 표현을 거꾸로(머리 + 보호 → 안전모), 단어표를 거꾸로(입다 → 착용). 원래 말도 같이 */
export function docWords(ws, domain) {
  let out = [...ws];
  for (const c of (domain && domain.combos) || []) {
    const at = c.words.map((w) => out.indexOf(w));
    if (at.every((i) => i >= 0)) { out = out.filter((w) => !c.words.includes(w)); out.splice(Math.min(...at), 0, c.as); }
  }
  const map = (domain && domain.to_doc) || {};
  return [...new Set(out.flatMap((w) => (map[w] ? [map[w], w] : [w])))];
}
/** 질문에 낱말이 나오는지: 그대로, 또는 '~다' 말은 활용형으로 (높다 → 높은·높아, 입다 → 입는·입어) */
const FORMS = ["은", "는", "을", "어", "아", "고", "게", "지", "다", "으"];
export function mentions(q, w) {
  if (q.includes(w)) return true;
  if (w.length >= 2 && w.endsWith("다")) { const stem = w.slice(0, -1); return FORMS.some((e) => q.includes(stem + e)); }
  return false;
}
const bigrams = (s) => { s = s.replace(/\s/g, ""); const out = new Set(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); return out; };
/** 낱말 → 비슷한 질문 (드문 말을 무겁게). [[점수, 질문]...] */
export function rankQuestions(ws, questions, k = 3) {   // k = Infinity 면 전부
  if (!ws.length) return [];
  const hit = (q, w) => (mentions(q, w) ? 1 : [...bigrams(w)].some((b) => bigrams(q).has(b)) ? 0.5 : 0);
  const weight = Object.fromEntries(ws.map((w) => [w, Math.log((questions.length + 1) / (1 + questions.filter((q) => q.includes(w)).length)) + 0.1]));
  const total = ws.reduce((a, w) => a + weight[w], 0) || 1;
  return questions.map((q) => [ws.reduce((a, w) => a + weight[w] * hit(q, w), 0) / total, q]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, k);
}
/** 질문 흐름으로 후보 다시 고르기: 낱말마다 후보 중에서 '우리 질문들에 같이 나오는 낱말 조합'을 위로.
 *  점수 = Σ log(후보 확률) + lam × Σ log(1 + 앞 낱말과 같은 질문에 나온 횟수). locked 는 사용자가 고른 낱말 (그대로) */
export function rerank(items, questions, domain, lam = 0.6) {
  if (items.length < 2 || !questions.length) return items.map((it) => it.label);
  const docs = questions.map((q) => q);
  const co = (a, b) => {
    const da = docWords([base(a)], domain), db = docWords([base(b)], domain);
    return docs.filter((q) => da.some((w) => q.includes(w)) && db.some((w) => q.includes(w))).length;
  };
  const opts = items.map((it) => (it.locked ? [{ label: it.label, p: 1 }] : it.cands.length ? it.cands : [{ label: it.label, p: 1 }]));
  let prev = opts[0].map((c) => ({ s: Math.log(c.p + 1e-6), path: [c.label] }));
  for (let i = 1; i < opts.length; i++) {
    prev = opts[i].map((c) => {
      let best = null;
      for (const pv of prev) {
        const s = pv.s + Math.log(c.p + 1e-6) + lam * Math.log(1 + co(pv.path[pv.path.length - 1], c.label));
        if (!best || s > best.s) best = { s, path: [...pv.path, c.label] };
      }
      return best;
    });
  }
  return prev.sort((a, b) => b.s - a.s)[0].path;
}

// ---------------- 낱말 하나 인식 (화면·평가 공통) ----------------
/** feats(특징 프레임들) → {label, cands, confident, custom}. opts: {domain, calibrate, custom, customSim} */
export async function classify(rec, feats, opts, domainIdx, protos) {
  const logits = await rec.logits(resampleSequence(feats));
  const r = candidates(logits, rec.labels, { allowed: opts.domain && domainIdx && domainIdx.length ? domainIdx : null, calibrate: opts.calibrate });
  let { cands, confident } = r, custom = false;
  if (opts.custom && protos && protos.length) {
    const m = matchCustom(logits, protos);
    if (m && m.sim >= (opts.customSim ?? 0.85)) { cands = [{ label: m.word, p: m.sim, custom: true }, ...cands].slice(0, 5); confident = true; custom = true; }
  }
  return { label: cands[0] ? cands[0].label : "?", cands, confident, custom, logits };
}

/** 예전 방식 (팀원 useRecognizer.ts): 0.25초마다 최근 48프레임을 보고, 같은 낱말이 연속 2번 확실하면 확정 */
export class WindowCommitter {
  constructor() { this.reset(); }
  reset() { this.buf = []; this.last = -1e9; this.pending = { label: "", hits: 0 }; this.lastCommitted = ""; }
  /** 프레임 하나 → 확정되면 인식 결과, 아니면 null. classifyFn(feats) 는 classify 를 부르는 함수 */
  async push(feat, t, classifyFn) {
    if (feat) { this.buf.push(feat); if (this.buf.length > 48) this.buf.shift(); }
    if (t - this.last < 250 || this.buf.length < 12) return null;
    this.last = t;
    const hand = this.buf.filter((f) => f[FEATURE_DIM - 2] > 0 || f[FEATURE_DIM - 1] > 0).length / this.buf.length;
    if (hand < 0.25) { this.pending = { label: "", hits: 0 }; return null; }   // 손이 없으면 답하지 않음
    const r = await classifyFn(this.buf);
    if (!r.confident) { this.pending = { label: "", hits: 0 }; return null; }
    if (this.pending.label === r.label) this.pending.hits++; else this.pending = { label: r.label, hits: 1 };
    if (this.pending.hits >= 2 && r.label !== this.lastCommitted) { this.lastCommitted = r.label; return r; }
    return null;
  }
}

/** 기본 설정 (화면의 '설정' 과 평가 페이지가 같이 씀) */
// 관절 인식 기본은 가벼운 모드 (Holistic 은 얼굴 점까지 계산해 느려서 점이 손을 늦게 따라감)
export const DEFAULTS = { engine: "light", worker: true, segment: "motion", domain: true, calibrate: true, smooth: true, useZ: true, rerank: true, custom: true, customSim: 0.85 };
