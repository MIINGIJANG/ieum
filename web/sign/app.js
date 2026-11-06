// 카메라 수어 입력 화면 (시험판, 본 화면과 따로). 공통 부분은 core.js, 관절 점 엔진은 engines.js.
// 영상은 기기 밖으로 나가지 않음 (서버에는 고른 질문 글자만).
import { DEFAULTS, FrameCleaner, Recognizer, Segmenter, WindowCommitter, base, classify, docWords, embed, fromB64,
  frameToFeatures, rankQuestions, rerank, resampleSequence, toB64 } from "./core.js";
import { createEngine } from "./engines.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const API = new URL("../", location.href).href;
const BASE = "./";

// ---------------- 설정 (기기에 기억) ----------------
const opts = { ...DEFAULTS };
try { Object.assign(opts, JSON.parse(localStorage.getItem("ieum-sign-opts") || "{}")); } catch { /* 저장 못 해도 기본값 */ }
const OPT_IDS = { engine: "optEngine", worker: "optWorker", segment: "optSegment", domain: "domainOnly", calibrate: "optCalibrate",
  smooth: "optSmooth", useZ: "optZ", rerank: "optRerank", custom: "optCustom" };
for (const [k, id] of Object.entries(OPT_IDS)) {
  const el = $(id);
  if (!el) continue;
  if (el.type === "checkbox") el.checked = !!opts[k]; else el.value = opts[k];
  el.onchange = () => {
    opts[k] = el.type === "checkbox" ? el.checked : el.value;
    try { localStorage.setItem("ieum-sign-opts", JSON.stringify(opts)); } catch { /* 무시 */ }
    if (k === "engine" || k === "worker") restartEngine();
    if (k === "segment") { seg.reset(); win.reset(); }
    if (k === "rerank") renderWords();
  };
}

// ---------------- 상태 ----------------
const rec = new Recognizer();
let domain = null, domainIdx = null, engine = null, worker = null, workerBusy = false, stream = null, raf = 0;
const cleaner = new FrameCleaner(), seg = new Segmenter(), win = new WindowCommitter();
const recent = [];                                      // 미리 보기용 최근 1.5초 특징
const items = [];                                       // 알아들은 낱말 [{label, cands, confident, locked, custom}]
let recording = null, teaching = null, classifying = false, lastPreview = 0;
const fpsTimes = [];

// 새 낱말 (기기에 저장 + web/sign/custom_words.json 기본값)
let protos = [];
function saveProtos() { try { localStorage.setItem("ieum-sign-protos", JSON.stringify(protos.map((p) => ({ word: p.word, samples: p.samples.map(toB64) })))); } catch { /* 무시 */ } renderProtos(); }
function loadProtosFrom(list) { return (list || []).map((p) => ({ word: p.word, samples: p.samples.map(fromB64) })); }

const domainReady = fetch(BASE + "domain.json").then((r) => r.json()).then((d) => { domain = d; renderWords(); }).catch(() => null);
fetch(BASE + "custom_words.json").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((d) => {
  let mine = [];
  try { mine = JSON.parse(localStorage.getItem("ieum-sign-protos") || "[]"); } catch { /* 무시 */ }
  const map = new Map();
  for (const p of [...loadProtosFrom(d && d.words), ...loadProtosFrom(mine)]) map.set(p.word, p);
  protos = [...map.values()];
  renderProtos();
});
const word = (l) => (domain && domain.words[l]) || base(l);

// ---------------- 불러오기 ----------------
function setLoad(text, pct) { $("loadMsg").textContent = text; if (pct != null) $("loadBar").style.width = `${pct}%`; }
async function loadModels() {
  await domainReady;
  setLoad("수어 단어 인식 모델 불러오는 중…", 5);
  await rec.load(BASE, (g, t) => t && setLoad("수어 단어 인식 모델 불러오는 중…", 5 + 60 * g / t));
  if (domain) { const s = new Set(domain.labels); domainIdx = rec.labels.map((l, i) => (s.has(l) ? i : -1)).filter((i) => i >= 0); }
  $("vocabInfo").textContent = `전체 ${rec.meta.num_classes.toLocaleString()}개 · 산업안전 ${domainIdx ? domainIdx.length : 0}개 (검증 정확도 ${(rec.meta.val_top1 * 100).toFixed(1)}%, 전체 단어 기준)`;
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
      $("engineInfo").textContent = `${opts.engine === "light" ? "가벼운 모드" : "Holistic"} · 작업자 스레드`;
      return;
    } catch (e) { console.warn("작업자 스레드를 못 써서 화면에서 계산", e); if (worker) worker.terminate(); worker = null; }
  }
  engine = await createEngine(opts.engine, BASE);
  $("engineInfo").textContent = `${opts.engine === "light" ? "가벼운 모드" : "Holistic"} · 화면 스레드`;
}
async function restartEngine() {
  if (!rec.session) return;
  if (worker) { worker.terminate(); worker = null; }
  if (engine) { engine.close(); engine = null; }
  cleaner.reset(); seg.reset(); win.reset();
  await startEngine();
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

const r4 = (v) => Math.round(v * 1e4) / 1e4;
function onFrame(raw, t) {
  if (recording) recording.frames.push({ t: Math.round(t - recording.t0), pose: raw.pose.map((p) => [r4(p.x), r4(p.y), r4(p.z), r4(p.visibility ?? 1)]),
    lh: raw.leftHand.length ? raw.leftHand.map((p) => [r4(p.x), r4(p.y), r4(p.z)]) : null, rh: raw.rightHand.length ? raw.rightHand.map((p) => [r4(p.x), r4(p.y), r4(p.z)]) : null });
  draw(raw);
  const frame = opts.smooth ? cleaner.clean(raw, t) : raw;
  const feat = frameToFeatures(frame, opts.useZ);
  fpsTimes.push(t); while (fpsTimes.length && t - fpsTimes[0] > 1000) fpsTimes.shift();
  $("fps").textContent = `${fpsTimes.length}fps · 몸 ${frame.pose.length ? "✓" : "✗"} · 왼손 ${frame.leftHand.length ? "✓" : "✗"} · 오른손 ${frame.rightHand.length ? "✓" : "✗"}`;
  $("handHint").hidden = !!(frame.leftHand.length || frame.rightHand.length);
  if (feat) { recent.push({ t, feat }); while (recent.length && t - recent[0].t > 1500) recent.shift(); }
  if (!rec.session) return;

  if (opts.segment === "motion") {
    const s = seg.push(frame, feat, t);
    $("segState").textContent = seg.cur ? "낱말 하는 중…" : seg.waitMove ? "다음 낱말을 하세요" : "손을 올려 시작";
    if (s) onSegment(s.frames);
  } else if (!classifying) {
    classifying = true;
    win.push(feat, t, (fs) => classify(rec, fs, opts, domainIdx, protos)).then((r) => r && addItem(r)).finally(() => { classifying = false; });
    $("segState").textContent = "예전 방식 (최근 48프레임)";
  }
  if (t - lastPreview > 300 && recent.length >= 8 && !classifying) {   // 미리 보기 (확정 아님)
    lastPreview = t; classifying = true;
    classify(rec, recent.map((r) => r.feat), opts, domainIdx, protos)
      .then((r) => { $("live").innerHTML = r.cands.slice(0, 3).map((c) => `${esc(word(c.label))} <small>${Math.round(c.p * 100)}%</small>`).join(" · "); })
      .finally(() => { classifying = false; });
  }
}

async function onSegment(feats) {
  if (teaching) {                                        // 새 낱말 가르치기: 예시로 저장
    const logits = await rec.logits(resampleSequence(feats));
    teaching.samples.push(embed(logits));
    $("teachMsg").textContent = `'${teaching.word}' 예시 ${teaching.samples.length}/5`;
    if (teaching.samples.length >= 5) {
      protos = protos.filter((p) => p.word !== teaching.word).concat([{ word: teaching.word, samples: teaching.samples }]);
      $("teachMsg").textContent = `'${teaching.word}' 를 배웠습니다 (예시 5개)`;
      teaching = null; saveProtos();
    }
    return;
  }
  addItem(await classify(rec, feats, opts, domainIdx, protos));
}
function addItem(r) {
  items.push({ label: r.label, cands: r.cands.map((c) => ({ label: c.label, p: c.p })), confident: r.confident, locked: false, custom: r.custom });
  renderWords();
  if (navigator.vibrate) navigator.vibrate(30);
}

// 손 21점 잇는 선 (엄지·검지·중지·약지·소지 + 손바닥), 몸은 어깨·팔·엉덩이
const HAND_LINES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]];
const POSE_LINES = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24]];
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
  const all = [...Array(21).keys()];
  if (frame.pose.length) { lines(frame.pose, POSE_LINES, "rgba(243,154,69,.8)", 3); dots(frame.pose, [0, 11, 12, 13, 14, 15, 16, 23, 24], 4, "#f39a45"); }
  for (const [hand, color] of [[frame.leftHand, "#4aa3ff"], [frame.rightHand, "#44d17a"]]) {
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
    $("loadBox").hidden = true; $("run").hidden = false;
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
  $("run").hidden = true; $("loadBox").hidden = false; $("start").disabled = false; $("start").textContent = "카메라 다시 켜기";
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
  $("words").innerHTML = items.map((it, i) => `<span class="chip${it.confident ? "" : " unsure"}"><button class="w" data-i="${i}" title="${it.confident ? "" : "확실하지 않아요 — 눌러서 후보 확인"}">${esc(word(ch[i]))}${it.confident ? "" : "?"}</button><button class="x" data-x="${i}" aria-label="지우기">✕</button></span>`).join("")
    || `<span class="empty">수어를 하면 여기에 낱말이 쌓입니다</span>`;
  $("alts").hidden = true;
  suggest(ch);
}
$("words").onclick = (e) => {
  const x = e.target.closest("[data-x]"), w = e.target.closest("[data-i]");
  if (x) { items.splice(+x.dataset.x, 1); win.lastCommitted = ""; renderWords(); return; }
  if (w) {
    const i = +w.dataset.i;
    $("alts").hidden = false;
    $("alts").innerHTML = `<span>다른 후보:</span>` + items[i].cands.map((c) => `<button data-r="${i}" data-l="${esc(c.label)}">${esc(word(c.label))} <small>${Math.round(c.p * 100)}%</small></button>`).join("");
  }
};
$("alts").onclick = (e) => {
  const b = e.target.closest("[data-r]");
  if (!b) return;
  const it = items[+b.dataset.r];
  it.label = b.dataset.l; it.locked = true; it.confident = true;   // 사용자가 고른 낱말은 그대로
  renderWords();
};
$("clear").onclick = () => { items.length = 0; win.lastCommitted = ""; renderWords(); $("answer").hidden = true; };

fetch(API + "examples").then((r) => r.json()).then((x) => { examples = [...new Set([...x.map((e) => e.question), ...examples])]; renderWords(); }).catch(() => {});
domainReady.then(() => { examples = [...new Set([...examples, ...((domain && domain.questions) || [])])]; renderWords(); });
function suggest(ch) {
  const ws = docWords(ch.map(word), domain), box = $("suggest");
  if (!ws.length) { box.innerHTML = ""; return; }
  box.innerHTML = rankQuestions(ws, examples).map(([, q]) => `<button class="q" data-q="${esc(q)}">${esc(q)}</button>`).join("")
    + `<button class="q direct" data-q="${esc(ws.join(" "))}">낱말로 바로 묻기: “${esc(ws.join(" "))}”</button>`;
}
$("suggest").onclick = async (e) => {
  const b = e.target.closest("[data-q]");
  if (!b) return;
  const q = b.dataset.q, a = $("answer");
  a.hidden = false; a.innerHTML = `<p>“${esc(q)}” 답을 찾는 중…</p>`;
  try {
    const d = await (await fetch(API + "query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: q }) })).json();
    const blocks = (d.sentences || []).flatMap((s) => (s.words || []).filter(Boolean));
    a.innerHTML = `<p class="q">질문: ${esc(q)}</p><p>${esc(d.answer_text || "관련 내용을 찾지 못했습니다.")}</p>`
      + (blocks.length ? `<p class="blocks">수어 블록: ${blocks.map(esc).join(" · ")}</p>` : "");
  } catch { a.innerHTML = `<p>서버에 묻지 못했습니다.</p>`; }
};

// ---------------- 녹화 (관절 점만, 평가용) ----------------
domainReady.then(() => { for (const q of (domain && domain.questions) || []) $("recQ").insertAdjacentHTML("beforeend", `<option>${esc(q)}</option>`); });
$("recBtn").onclick = () => {
  if (!recording) {
    recording = { version: 1, engine: opts.engine, created: new Date().toISOString(),
      label: { question: $("recQ").value, words: $("recWords").value.trim() }, t0: performance.now(), frames: [] };
    $("recBtn").textContent = "녹화 끝"; $("recMsg").textContent = "녹화 중… (영상은 저장하지 않고 관절 점 숫자만)";
    return;
  }
  const r = recording; recording = null; delete r.t0;
  $("recBtn").textContent = "녹화 시작";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(r)], { type: "application/json" }));
  a.download = `sign_${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  a.click();
  $("recMsg").textContent = `${r.frames.length}프레임 저장 → 평가 페이지(eval.html)에 넣으세요`;
};

// ---------------- 새 낱말 가르치기 ----------------
function renderProtos() {
  $("protoList").innerHTML = protos.length
    ? protos.map((p) => `<span class="chip"><span class="w">${esc(p.word)} <small>${p.samples.length}</small></span><button class="x" data-del="${esc(p.word)}">✕</button></span>`).join("")
    : `<span class="empty">아직 없음</span>`;
}
$("protoList").onclick = (e) => { const b = e.target.closest("[data-del]"); if (b) { protos = protos.filter((p) => p.word !== b.dataset.del); saveProtos(); } };
$("teachBtn").onclick = () => {
  const w = $("teachWord").value.trim();
  if (!w) { $("teachMsg").textContent = "낱말을 먼저 적으세요"; return; }
  if (opts.segment !== "motion") { $("teachMsg").textContent = "'손 움직임으로 끊기'를 켜야 예시를 모을 수 있어요"; return; }
  teaching = { word: w, samples: [] };
  $("teachMsg").textContent = `'${w}' 수어를 5번 하세요 (한 번 하고 손을 내리기)`;
};
$("protoExport").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify({ words: protos.map((p) => ({ word: p.word, samples: p.samples.map(toB64) })) })], { type: "application/json" }));
  a.download = "custom_words.json"; a.click();
};
$("protoImport").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const d = JSON.parse(await f.text());
  for (const p of loadProtosFrom(d.words)) protos = protos.filter((x) => x.word !== p.word).concat([p]);
  saveProtos();
};

$("start").onclick = startCamera;
$("stop").onclick = stopCamera;
renderWords();
window.__sign = { opts, items, renderWords, draw, docWords: (ws) => docWords(ws, domain), rec, addItem,
  classify: (f) => classify(rec, f, opts, domainIdx, protos) };
