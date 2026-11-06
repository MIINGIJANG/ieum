// 카메라 수어 입력 화면 (시험판, 본 화면과 따로). 공통 부분은 core.js, 관절 점 엔진은 engines.js.
// 영상은 기기 밖으로 나가지 않음 (서버에는 고른 질문 글자만).
import { DEFAULTS, FrameCleaner, Recognizer, Segmenter, WindowCommitter, base, classify, docWords, fromB64,
  frameToFeatures, rankQuestions, rerank } from "./core.js";
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
const recent = [];                                      // 미리 보기용 최근 1.5초 특징
const items = [];                                       // 알아들은 낱말 [{label, cands, confident, locked, custom}]
let classifying = false, lastPreview = 0;
const fpsTimes = [];

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
  addItem(await classify(rec, feats, opts, domainIdx, protos));
}
function addItem(r) {
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
    $("loadBox").hidden = true; $("stop").hidden = false; $("fps").textContent = "카메라 켜짐";
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
  $("fps").textContent = "카메라 꺼짐"; $("segState").textContent = "손을 올려 시작"; $("live").textContent = "—";
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


$("start").onclick = startCamera;
$("stop").onclick = stopCamera;
renderWords();
window.__sign = { opts, items, renderWords, draw, docWords: (ws) => docWords(ws, domain), rec, addItem,
  classify: (f) => classify(rec, f, opts, domainIdx, protos) };
