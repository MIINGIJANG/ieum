// 카메라 수어 입력 화면 (시험판, 본 화면과 따로). 공통 부분은 core.js, 관절 점 엔진은 engines.js.
// 영상은 기기 밖으로 나가지 않음 (서버에는 고른 질문 글자만).
import { DEFAULTS, FrameCleaner, Recognizer, Segmenter, WindowCommitter, base, classify, docWords, fromB64,
  frameToFeatures, mentions, rankQuestions, rerank } from "./core.js";
import { createEngine } from "./engines.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const API = new URL("../", location.href).href;
const BASE = "./";

// ---------------- 설정 (기본값 고정, 평가 페이지 eval.html 에서만 바꿔 비교) ----------------
const opts = { ...DEFAULTS };

// ---------------- 상태 ----------------
const rec = new Recognizer();
let domain = null, domainIdx = null, engine = null, worker = null, workerBusy = false, stream = null, raf = 0;
const cleaner = new FrameCleaner(), seg = new Segmenter(), win = new WindowCommitter();
const items = [];                                       // 알아들은 낱말 [{label, cands, confident, locked, custom}]
let classifying = false;

// 새 낱말 (web/sign/custom_words.json + 예전에 이 기기에서 가르친 것)
let protos = [];
function loadProtosFrom(list) { return (list || []).map((p) => ({ word: p.word, samples: p.samples.map(fromB64) })); }

const domainReady = fetch(BASE + "domain.json").then((r) => r.json()).then((d) => { domain = d; renderWords(); }).catch(() => null);
fetch(BASE + "custom_words.json").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((d) => {
  let mine = [];
  try { mine = JSON.parse(localStorage.getItem("ieum-sign-protos") || "[]"); } catch { /* 무시 */ }
  const map = new Map();
  for (const p of [...loadProtosFrom(d && d.words), ...loadProtosFrom(mine)]) map.set(p.word, p);
  protos = [...map.values()];
});
const word = (l) => (domain && domain.words[l]) || base(l);

// ---------------- 불러오기 ----------------
function setLoad(text, pct) { $("loadMsg").textContent = text; if (pct != null) $("loadBar").style.width = `${pct}%`; }
async function loadModels() {
  await domainReady;
  setLoad("수어 단어 인식 모델 불러오는 중…", 5);
  await rec.load(BASE, (g, t) => t && setLoad("수어 단어 인식 모델 불러오는 중…", 5 + 60 * g / t));
  if (domain) { const s = new Set(domain.labels); domainIdx = rec.labels.map((l, i) => (s.has(l) ? i : -1)).filter((i) => i >= 0); }
  await startEngine();
  setLoad("준비 완료", 100);
}
async function startEngine() {
  setLoad(`관절 인식 모델 불러오는 중… (${opts.engine === "light" ? "가벼운 모드" : "Holistic"})`, 70);
  if (opts.worker && typeof Worker !== "undefined" && typeof createImageBitmap !== "undefined") {
    try {
      worker = new Worker(new URL("./landmark-worker.js", import.meta.url), { type: "module" });
      await new Promise((res, rej) => {
        worker.onmessage = (e) => { if (e.data.type === "ready") res(); else if (e.data.type === "error") rej(new Error(e.data.message)); };
        worker.onerror = (e) => rej(new Error(e.message || "작업자 스레드 오류"));
        worker.postMessage({ type: "init", kind: opts.engine, base: BASE });
      });
      worker.onmessage = (e) => {
        workerBusy = false;
        if (e.data.type === "frame") onFrame(e.data.frame, e.data.ts);
        else if (e.data.type === "error") console.warn("작업자 스레드:", e.data.message);
      };
      return;
    } catch (e) { console.warn("작업자 스레드를 못 써서 화면에서 계산", e); if (worker) worker.terminate(); worker = null; }
  }
  engine = await createEngine(opts.engine, BASE);
}

// ---------------- 프레임마다 ----------------
let lastTs = -1;
function loop() {
  raf = requestAnimationFrame(loop);
  const v = $("video");
  if (v.readyState < 2) return;
  const ts = performance.now();
  if (ts <= lastTs) return;
  if (worker) {
    if (workerBusy) return;                              // 앞 프레임 계산 중이면 건너뜀 (밀리지 않게)
    workerBusy = true; lastTs = ts;
    createImageBitmap(v).then((bm) => worker.postMessage({ type: "frame", bitmap: bm, ts }, [bm])).catch(() => { workerBusy = false; });
    return;
  }
  if (!engine) return;
  lastTs = ts;
  let frame;
  try { frame = engine.detect(v, ts); } catch { return; }
  onFrame(frame, ts);
}

function onFrame(raw, t) {
  draw(raw);
  const frame = opts.smooth ? cleaner.clean(raw, t) : raw;
  const feat = frameToFeatures(frame, opts.useZ);
  $("handHint").hidden = !!(frame.leftHand.length || frame.rightHand.length);
  if (!rec.session) return;

  if (opts.segment === "motion") {
    const s = seg.push(frame, feat, t);
    if (s) onSegment(s.frames);
  } else if (!classifying) {
    classifying = true;
    win.push(feat, t, (fs) => classify(rec, fs, opts, domainIdx, protos)).then((r) => r && addItem(r)).finally(() => { classifying = false; });
  }
}

async function onSegment(feats) {
  addItem(await classify(rec, feats, opts, domainIdx, protos));
}
function addItem(r) {
  // 한 낱말을 하는 중에 잠깐 멈추면 여러 조각으로 끊겨 같은 낱말이 여러 번 나옴 → 이미 있는 낱말이면 다시 넣지 않음
  if (items.some((it) => word(it.label) === word(r.label))) return;
  items.push({ label: r.label, cands: r.cands.map((c) => ({ label: c.label, p: c.p })), confident: r.confident, locked: false, custom: r.custom });
  renderWords();
  if (navigator.vibrate) navigator.vibrate(30);
}

// 손 21점 잇는 선 (엄지·검지·중지·약지·소지 + 손바닥 둘레: 손목–엄지–검지 뿌리–…–소지 뿌리–손목). 엄지 맨 아래 점(1)은 그리지 않고 손목에서 바로 2로
const HAND_LINES = [[0, 2], [2, 3], [3, 4], [2, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]];
/** 화면에는 **정리 전 관절 점**을 그림 (떨림 줄이기는 인식에만 씀 → 점이 손을 늦게 따라가지 않게) */
function draw(frame) {
  const v = $("video"), cv = $("overlay"), w = v.videoWidth || 640, h = v.videoHeight || 480;
  if (cv.width !== w) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.lineCap = "round";
  const lines = (pts, pairs, color, width) => {
    g.strokeStyle = color; g.lineWidth = width; g.beginPath();
    for (const [a, b] of pairs) if (pts[a] && pts[b]) { g.moveTo(pts[a].x * w, pts[a].y * h); g.lineTo(pts[b].x * w, pts[b].y * h); }
    g.stroke();
  };
  const dots = (pts, idx, r, color) => { g.fillStyle = color; for (const i of idx) if (pts[i]) { g.beginPath(); g.arc(pts[i].x * w, pts[i].y * h, r, 0, 7); g.fill(); } };
  const all = [...Array(21).keys()].filter((i) => i !== 1);   // 엄지 맨 아래 점(1)은 그리지 않음
  // 화면에는 손만 (몸 점은 인식에는 쓰지만 그리지 않음 — 화면이 덜 복잡하게), 왼손·오른손 모두 흰색
  for (const [hand, color] of [[frame.leftHand, "#ffffff"], [frame.rightHand, "#ffffff"]]) {
    if (hand.length !== 21) continue;
    lines(hand, HAND_LINES, color, 2.5);
    dots(hand, all, 2.5, color);
  }
}

async function startCamera() {
  $("start").disabled = true;
  try {
    if (!rec.session) await loadModels();
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
    const v = $("video");
    v.srcObject = stream;
    await v.play();
    $("loadBox").hidden = true; $("stop").hidden = false;
    cleaner.reset(); seg.reset(); win.reset();
    raf = requestAnimationFrame(loop);
  } catch (e) {
    console.error(e);
    setLoad(`시작하지 못했습니다: ${e.message || e}`, null);
    $("start").disabled = false;
  }
}
function stopCamera() {
  cancelAnimationFrame(raf);
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null; $("video").srcObject = null;
  const cv = $("overlay"); cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);   // 꺼지면 손 선도 지움
  $("handHint").hidden = true;
  $("stop").hidden = true; $("loadBox").hidden = false; $("start").disabled = false; setLoad("카메라가 꺼져 있습니다", null);
}

// ---------------- 낱말 · 질문 ----------------
let examples = [];
function chosen() {
  if (opts.rerank && items.length > 1) {
    const path = rerank(items, (domain && domain.questions) || examples, domain);
    return items.map((it, i) => (it.locked ? it.label : path[i]));
  }
  return items.map((it) => it.label);
}
function renderWords() {
  const ch = chosen();
  $("words").innerHTML = items.map((it, i) => `<span class="chip${it.confident ? "" : " unsure"}${it.sel ? " sel" : ""}"><button class="w" data-i="${i}">${esc(word(ch[i]))}${it.confident ? "" : "?"}</button></span>`).join("")
    || `<span class="empty">수어를 하면 여기에 낱말이 쌓입니다</span>`;
  suggest(ch);
}
// 낱말을 누르면 고르기/풀기 (고른 순서대로 질문을 찾음)
let selOrder = 0;
$("words").onclick = (e) => {
  const w = e.target.closest("[data-i]");
  if (w) {
    const i = +w.dataset.i, it = items[i];
    if (!it.sel) { it.label = chosen()[i]; it.locked = true; }   // 고른 낱말은 뒤에 낱말이 더 들어와도 바뀌지 않게
    it.sel = it.sel ? 0 : ++selOrder; renderWords();
  }
};

fetch(API + "examples").then((r) => r.json()).then((x) => { examples = [...new Set([...x.map((e) => e.question), ...examples])]; renderWords(); }).catch(() => {});
domainReady.then(() => { examples = [...new Set([...examples, ...((domain && domain.questions) || [])])]; renderWords(); });
/** 고른 낱말로 질문 블럭: 비슷한 질문 + 맨 뒤에 고른 낱말을 이어 붙인 블럭 (항상 있음) */
function suggest(ch) {
  const picked = items.map((it, i) => [it.sel, word(ch[i])]).filter(([s]) => s).sort((a, b) => a[0] - b[0]).map(([, w]) => w);
  const box = $("suggest");
  if (!picked.length) { box.innerHTML = ""; return; }
  const joined = picked.join(" ");
  // 고른 낱말을 많이 담은 질문부터 (낱말마다 문서 말·활용형도 인정: 입다 → 착용·입는), 같으면 드문 말 점수 순
  const groups = picked.map((w) => docWords([w], domain));
  const covered = (q) => groups.filter((g) => g.some((w) => mentions(q, w))).length;
  const qs = rankQuestions(docWords(picked, domain), examples, Infinity)
    .map(([sc, q]) => [covered(q), sc, q]).filter(([c]) => c > 0)
    .sort((a, b) => b[0] - a[0] || b[1] - a[1]).slice(0, 3).map(([, , q]) => q).filter((q) => q !== joined);
  box.innerHTML = [...qs, joined].map((q) => `<button class="q" data-q="${esc(q)}">${esc(q)}</button>`).join("");
}
// 질문 블럭을 누르면 팝업이 닫히고 입력창에 들어감 (보내기는 사용자가)
$("suggest").onclick = (e) => {
  const b = e.target.closest("[data-q]");
  if (!b) return;
  $("q").value = b.dataset.q;
  closePanel();
  $("q").focus();
};
// 팝업 열기·닫기 (애플 가이드: Designing Fluid Interfaces / HIG Motion)
//  - 카메라 버튼 아래(입력창 아랫면)에서 커져 나오고, 닫을 때 같은 길로 그리로 들어감
//  - 튕김 없는 스프링 (damping 1.0, response 0.35초), 열기·닫기 같은 곡선
//  - 움직이는 도중 다시 누르면 지금 위치·속도에서 그대로 되돌아감 (처음으로 튀지 않음)
const SPRING = { response: 0.35, damping: 1 };
const POP_SCALE = { from: 0.5, to: 0.2 };   // 나타날 때 50%에서 시작, 사라질 때는 20%까지 더 작아지며 버튼으로 들어감
// 크기·투명도를 각각 스프링으로 (도중에 방향이 바뀌어도 둘 다 지금 값·속도에서 이어짐)
let panelOpen = true, pop = { s: 1, sv: 0, o: 1, ov: 0, ts: 1, to: 1, raf: 0, last: 0 };
// 나오고 들어가는 점: 카메라 버튼 바로 아래, 입력창 아랫면 (팝업이 입력창 안으로 겹쳐 들어가지 않게)
function popOrigin() {   // 크기가 바뀌는 중에도 맞게: 화면 위치(getBoundingClientRect) 대신 배치 위치(offset)로
  const p = $("panel"), f = $("form"), b = $("camBtn");
  return `${f.offsetLeft + b.offsetLeft + b.offsetWidth / 2 - p.offsetLeft}px ${f.offsetTop + f.offsetHeight - p.offsetTop}px`;
}
function popRender() {
  const el = $("panel");
  el.style.opacity = String(Math.min(1, Math.max(0, pop.o)));
  el.style.transform = Math.abs(pop.s - 1) < 0.001 ? "" : `scale(${pop.s})`;
  el.style.pointerEvents = pop.to ? "" : "none";
}
function popStep(t) {
  const dt = Math.min(0.032, (t - pop.last) / 1000 || 0.016); pop.last = t;
  const w = (2 * Math.PI) / SPRING.response, k = 2 * SPRING.damping * w;   // 애플의 response → 고유 진동수
  pop.sv += (-w * w * (pop.s - pop.ts) - k * pop.sv) * dt; pop.s += pop.sv * dt;
  pop.ov += (-w * w * (pop.o - pop.to) - k * pop.ov) * dt; pop.o += pop.ov * dt;
  const done = Math.abs(pop.s - pop.ts) < 0.001 && Math.abs(pop.sv) < 0.01 && Math.abs(pop.o - pop.to) < 0.001 && Math.abs(pop.ov) < 0.01;
  if (done) {
    pop.s = pop.ts; pop.o = pop.to; pop.sv = pop.ov = 0; pop.raf = 0; popRender();
    if (!pop.to) $("panel").hidden = true;
    return;
  }
  popRender();
  pop.raf = requestAnimationFrame(popStep);
}
function popTo(open) {
  const el = $("panel");
  if (open && el.hidden) { el.hidden = false; pop.s = POP_SCALE.from; pop.o = 0; pop.sv = pop.ov = 0; }   // 완전히 사라진 뒤 다시 열 때만 50%에서 시작
  pop.ts = open ? 1 : POP_SCALE.to; pop.to = open ? 1 : 0;
  el.style.transformOrigin = popOrigin();
  if (!pop.raf) { pop.last = performance.now(); pop.raf = requestAnimationFrame(popStep); }   // 움직이는 중이면 지금 값·속도 그대로 목표만 바꿈
}
function openPanel() { panelOpen = true; $("camBtn").classList.add("on"); popTo(true); }
function closePanel() {
  if (stream) stopCamera();
  if (!panelOpen) return;
  panelOpen = false; $("camBtn").classList.remove("on");
  popTo(false);
}
$("camBtn").onclick = () => (panelOpen ? closePanel() : openPanel());

$("start").onclick = startCamera;
$("stop").onclick = stopCamera;
// 시험용: 카메라로 낱말 몇 개를 이미 알아들은 상태로 시작 (본 화면에 붙일 때는 지움)
for (const l of ["보호1", "입다1", "위험1", "높다1", "언제1", "확인1"])
  items.push({ label: l, cands: [{ label: l, p: 1 }], confident: true, locked: true });
renderWords();
window.__sign = { opts, items, renderWords, draw, docWords: (ws) => docWords(ws, domain), rec, addItem,
  classify: (f) => classify(rec, f, opts, domainIdx, protos) };
