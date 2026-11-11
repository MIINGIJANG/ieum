// 카메라 수어 입력 팝업 (본 화면 web/index.html 과 시험 페이지 web/sign/index.html 이 같이 씀). 모양은 popup.css,
// 공통 부분은 core.js, 관절 점 엔진은 engines.js. 모델은 페이지를 열 때 미리 준비 (본 화면은 첫 로딩 화면에서) → 카메라 버튼을 누르면 바로 켜짐.
// 영상은 기기 밖으로 나가지 않음 (서버에는 고른 질문 글자만).
import { DEFAULTS, FrameCleaner, Recognizer, Segmenter, WindowCommitter, base, classify, docWords, fromB64,
  frameToFeatures, mentionAt, mentions, rankQuestions, rerank } from "./core.js";
import { createEngine } from "./engines.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const API = new URL("../", import.meta.url).href;   // 어느 페이지에서 불러도 같은 곳 (이 파일 기준)
const BASE = new URL("./", import.meta.url).href;

// ---------------- 설정 (기본값 고정, 평가 페이지 eval.html 에서만 바꿔 비교) ----------------
const opts = { ...DEFAULTS };

// ---------------- 상태 ----------------
const rec = new Recognizer();
let domain = null, domainIdx = null, engine = null, worker = null, workerBusy = false, stream = null, raf = 0;
const cleaner = new FrameCleaner(), seg = new Segmenter(), win = new WindowCommitter();
const items = [];                                       // 알아들은 낱말 [{label, cands, confident, locked, custom}]
let classifying = false;

// 새 낱말 (web/sign/custom_words.json 만. 기기에 저장해 둔 것은 읽지 않음)
let protos = [];
function loadProtosFrom(list) { return (list || []).map((p) => ({ word: p.word, samples: p.samples.map(fromB64) })); }

const domainReady = fetch(BASE + "domain.json").then((r) => r.json()).then((d) => { domain = d; renderWords(); }).catch(() => null);
fetch(BASE + "custom_words.json").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((d) => {
  const map = new Map();
  for (const p of loadProtosFrom(d && d.words)) map.set(p.word, p);
  protos = [...map.values()];
});
const word = (l) => (domain && domain.words[l]) || base(l);

// ---------------- 불러오기 ----------------
function setLoad() {}   // 카메라 칸에는 안내 문구·불러오기 막대를 두지 않음 (불러오는 동안은 켜기 버튼만 눌리지 않게)
// 미리 준비: 모델 받기·인식 엔진 시작 (한 번만). 끝나면 'sign-ready' 알림 (본 화면 첫 로딩 화면이 기다림)
let readyP = null;
function prepare() {
  return (readyP ??= loadModels().then(
    () => dispatchEvent(new Event("sign-ready")),
    (e) => { console.error("카메라 수어 입력 준비 실패", e); readyP = null; dispatchEvent(new Event("sign-failed")); throw e; }));
}
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
      // 첫 계산 미리 (처음 한 번은 그래픽 준비로 오래 걸림 → 로딩 화면에서 끝내 둠)
      const blank = document.createElement("canvas"); blank.width = 640; blank.height = 480; blank.getContext("2d").fillRect(0, 0, 640, 480);
      await new Promise((res) => {
        worker.onmessage = (e) => { if (e.data.type === "frame" || e.data.type === "error") res(); };
        createImageBitmap(blank).then((bm) => worker.postMessage({ type: "frame", bitmap: bm, ts: 1 }, [bm])).catch(res);
        setTimeout(res, 15000);
      });
      worker.onmessage = (e) => {
        workerBusy = false;
        if (e.data.type === "frame") onFrame(e.data.frame, e.data.ts);
        else if (e.data.type === "error") console.warn("작업자 스레드:", e.data.message);
      };
      lastTs = 1;
      return;
    } catch (e) { console.warn("작업자 스레드를 못 써서 화면에서 계산", e); if (worker) worker.terminate(); worker = null; }
  }
  engine = await createEngine(opts.engine, BASE);
  const blank = document.createElement("canvas"); blank.width = 640; blank.height = 480; blank.getContext("2d").fillRect(0, 0, 640, 480);
  try { engine.detect(blank, 1); lastTs = 1; } catch { /* 첫 계산 미리 — 실패해도 그냥 진행 */ }
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
  if (!stream) return;   // 카메라를 끈 뒤에 늦게 도착한 계산 결과는 버림 (흰 선이 남지 않게)
  draw(raw);
  const frame = opts.smooth ? cleaner.clean(raw, t) : raw;
  const feat = frameToFeatures(frame, opts.useZ);
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
  // 화면에 보이는 낱말(질문 흐름으로 다른 후보로 바뀌어 보일 수 있음)과도 비교
  const shown = new Set([...items.map((it) => word(it.label)), ...chosen().map(word)]);
  if (shown.has(word(r.label))) return;
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
  // 팔: 손목(손 점 0, 손이 안 잡히면 몸의 손목) → 팔꿈치 → 어깨. 화면 밖이라 잘 안 보이는 몸 점은 잇지 않음
  const pose = frame.pose || [], seen = (q) => q && (q.visibility == null || q.visibility >= 0.5);
  for (const [hand, wr, el, sh] of [[frame.leftHand, 15, 13, 11], [frame.rightHand, 16, 14, 12]]) {
    const wrist = hand.length === 21 ? hand[0] : seen(pose[wr]) ? pose[wr] : null;
    const arm = [wrist, seen(pose[el]) ? pose[el] : null, seen(pose[sh]) ? pose[sh] : null];
    lines(arm, [[0, 1], [1, 2]], "#ffffff", 2.5);
    // 점은 선이 이어진 곳에만 (선 없이 어깨 점만 떠 있지 않게). 손이 잡히면 손목 점은 손 쪽에서 그림
    const on = new Set();
    for (const [i, j] of [[0, 1], [1, 2]]) if (arm[i] && arm[j]) { on.add(i); on.add(j); }
    if (hand.length === 21) on.delete(0);
    dots(arm, [...on], 2.5, "#ffffff");
  }
  // 손: 왼손·오른손 모두 흰색 (몸의 다른 점은 인식에는 쓰지만 그리지 않음 — 화면이 덜 복잡하게)
  for (const [hand, color] of [[frame.leftHand, "#ffffff"], [frame.rightHand, "#ffffff"]]) {
    if (hand.length !== 21) continue;
    lines(hand, HAND_LINES, color, 2.5);
    dots(hand, all, 2.5, color);
  }
}

// 카메라 켜기: 팝업을 열면 바로 (권한을 먼저 받으며 켠 카메라가 있으면 그것을 씀). 실패하면 팝업을 닫음 (카메라 없이 팝업만 떠 있지 않게)
let starting = false;
const VIDEO = { video: { width: 640, height: 480, facingMode: "user" }, audio: false };
async function startCamera(pre = null) {   // pre: 팝업을 열기 전에 권한을 받으며 이미 켠 카메라
  if (stream || starting) { if (pre && pre !== stream) pre.getTracks().forEach((t) => t.stop()); return; }
  starting = true;
  clearOverlay();   // 다시 켤 때도 처음처럼 빈 화면에서
  try {
    // 카메라 화면은 받는 즉시 보여 줌 (인식 모델 준비를 기다리지 않음). 손 인식은 모델이 준비되면 시작
    const s = pre || await navigator.mediaDevices.getUserMedia(VIDEO);
    if (!panelOpen) { s.getTracks().forEach((t) => t.stop()); return; }   // 켜지는 동안 팝업을 닫았으면 끔
    stream = s;
    const v = $("video");
    v.srcObject = stream;
    await v.play();
    await prepare();
    if (stream !== s) return;                                             // 준비하는 동안 꺼졌으면 그대로
    cleaner.reset(); seg.reset(); win.reset();
    raf = requestAnimationFrame(loop);
  } catch (e) {
    console.error(e);
    if (panelOpen) closePanel();
  } finally { starting = false; }
}
// 카메라 칸 비율 = 실제 카메라 영상 비율 (휴대폰은 세로 영상이라 칸이 세로로 길어짐 → 좌우 검은 띠 없음).
//  영상이 오기 전에는 지난번 비율(처음은 4:3) 그대로
const fitCamBox = () => {
  const v = $("video");
  if (v.videoWidth && v.videoHeight) document.querySelector("#panel .cam").style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`;
};
$("video").addEventListener("loadedmetadata", fitCamBox);
$("video").addEventListener("resize", fitCamBox);   // 휴대폰을 돌리는 등 영상 크기가 바뀔 때
function clearOverlay() { const cv = $("overlay"); cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); }
function stopCamera() {
  cancelAnimationFrame(raf);
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null; $("video").srcObject = null;
  clearOverlay(); workerBusy = false;   // 꺼지면 손 선도 지움
}

// ---------------- 낱말 · 질문 ----------------
let examples = [];
function chosen() {
  if (opts.rerank && items.length > 1) {
    const path = rerank(items, (domain && domain.questions) || examples, domain);
    const out = items.map((it, i) => (it.locked ? it.label : path[i]));
    // 다른 후보로 바꿔 보이다가 같은 낱말이 두 번 보이게 되면, 뒤의 것은 원래 알아들은 낱말로
    const seen = new Set();
    return out.map((l, i) => { const w = word(l); if (seen.has(w)) { seen.add(word(items[i].label)); return items[i].label; } seen.add(w); return l; });
  }
  return items.map((it) => it.label);
}
// 고른 낱말은 고른 순서대로 앞에, 풀린 낱말은 맨 뒤에. 자리가 바뀌면 스르륵 미끄러져 옮겨감
let chipSeq = 0;
function renderWords() {
  const ch = chosen(), box = $("words");
  const before = new Map([...box.querySelectorAll(".chip")].map((c) => [c.dataset.k, c.getBoundingClientRect()]));
  // 고른 낱말(고른 순서) → 한 번도 안 고른 낱말(알아들은 순서) → 풀린 낱말(푼 순서, 항상 맨 뒤)
  const key = (i) => { const it = items[i]; return it.sel ? [0, it.sel] : [1, it.tail || 0, i]; };
  const order = items.map((it, i) => i).sort((a, b) => { const x = key(a), y = key(b); for (let j = 0; j < 3; j++) if ((x[j] ?? 0) !== (y[j] ?? 0)) return (x[j] ?? 0) - (y[j] ?? 0); return 0; });
  const seenW = new Set();
  const chips = order.filter((i) => { const w = word(ch[i]); if (seenW.has(w)) return false; seenW.add(w); return true; }).map((i) => {   // 같은 낱말은 한 번만
    const it = items[i];
    it.k ??= ++chipSeq;
    return `<span class="chip${it.sel ? " sel" : ""}" data-k="${it.k}"><button class="w" data-i="${i}">${esc(word(ch[i]))}</button></span>`;   // 확실하지 않은 낱말도 따로 표시하지 않음
  });
  layoutRows(box, chips);
  if (items.length) showWords();   // 알아들은 낱말이 생기면 단어 칸이 내려옴
  for (const c of box.querySelectorAll(".chip")) {   // 예전 자리에서 새 자리로 (FLIP)
    const r0 = before.get(c.dataset.k); if (!r0) continue;
    const r1 = c.getBoundingClientRect(), dx = r0.left - r1.left, dy = r0.top - r1.top;
    if (Math.abs(dx) + Math.abs(dy) < 1) continue;
    c.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 450, easing: "cubic-bezier(.3,.7,.2,1)" });
  }
  suggest(ch);
}
/** 낱말 줄 나누기: 한 줄에 다 들어가면 한 줄. 넘치면 줄마다 개수를 고르게 나누고, 홀수면 위 줄이 하나 적게
 *  (예: 9개 → 4·5). 그래서 하나 늘면 아랫줄 끝에 붙고, 개수가 맞춰질 때는 아랫줄 첫 낱말이 윗줄로 올라감 (움직임은 낱말 이동 그대로) */
function layoutRows(box, chips) {
  box.classList.remove("rows");
  box.innerHTML = chips.join("");
  const lines = new Set([...box.children].map((c) => c.offsetTop)).size;
  if (lines < 2) return;
  box.classList.add("rows");
  for (let r = lines; r <= chips.length; r++) {
    const base = Math.floor(chips.length / r), extra = chips.length % r;   // 아래쪽 extra 줄이 하나씩 더
    const rows = []; let at = 0;
    for (let i = 0; i < r; i++) { const n = base + (i >= r - extra ? 1 : 0); rows.push(chips.slice(at, at + n)); at += n; }
    box.innerHTML = rows.map((row) => `<div class="wrow">${row.join("")}</div>`).join("");
    if ([...box.children].every((row) => row.scrollWidth <= row.clientWidth + 1)) return;   // 줄마다 넘치지 않으면 끝
  }
}
// 낱말을 누르면 고르기/풀기 (고른 순서대로 질문을 찾음)
let selOrder = 0;
$("words").onclick = (e) => {
  const w = e.target.closest("[data-i]");
  if (w) {
    const i = +w.dataset.i, it = items[i];
    if (!it.sel) { it.label = chosen()[i]; it.locked = true; }   // 고른 낱말은 뒤에 낱말이 더 들어와도 바뀌지 않게
    if (it.sel) { it.sel = 0; it.tail = ++selOrder; } else it.sel = ++selOrder;   // 풀면 맨 뒤로
    renderWords();
  }
};

fetch(API + "examples").then((r) => r.json()).then((x) => { examples = [...new Set([...x.map((e) => e.question), ...examples])]; renderWords(); }).catch(() => {});
domainReady.then(() => { examples = [...new Set([...examples, ...((domain && domain.questions) || [])])]; renderWords(); });
/** 고른 낱말로 질문 블럭: 비슷한 질문 + 맨 뒤에 고른 낱말을 이어 붙인 블럭 (항상 있음) */
function suggest(ch) {
  const picked = items.map((it, i) => [it.sel, word(ch[i])]).filter(([s]) => s).sort((a, b) => a[0] - b[0]).map(([, w]) => w);
  const box = $("suggest");
  if (!picked.length) { rollTo(box, ""); return; }
  // 고른 낱말을 많이 담은 질문부터 (낱말마다 문서 말·활용형도 인정: 입다 → 착용·입는), 같으면 드문 말 점수 순
  const groups = picked.map((w) => docWords([w], domain));
  const covered = (q) => groups.filter((g) => g.some((w) => mentions(q, w))).length;
  const ranked = rankQuestions(docWords(picked, domain), examples, Infinity)
    .map(([sc, q]) => [covered(q), sc, q]).filter(([c]) => c > 0).sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  const joined = orderWords(picked, groups, ranked.filter(([c]) => c === picked.length).map(([, , q]) => q)).join(" ");
  // 보여 줄 질문 3개는 그대로 고르고, 화면에는 긴 것부터 (맨 아래는 낱말을 이어 붙인 블럭)
  const qs = ranked.slice(0, 3).map(([, , q]) => q).filter((q) => q !== joined).sort((a, b) => textWidth(b) - textWidth(a));
  rollTo(box, `<p class="notice" data-k="notice">표현하신 질문을 클릭해 주세요.</p><section class="box">`
    + [...qs.map((q) => [q, q]), [joined, "__joined"]].map(([q, k]) => `<button class="q" data-q="${esc(q)}" data-k="${esc(k)}">${esc(q)}</button>`).join("") + `</section>`);
}
/** 고른 낱말을 말이 되는 순서로: 우리 질문들에서 두 낱말이 어느 쪽이 먼저 나오는지 세어 정함
 *  (고른 낱말을 모두 담은 질문은 크게 침). 질문에 같이 나온 적이 없으면 이름씨 → 묻는 말 → '~다'·'~하다' 말 순, 그다음 고른 순서 */
const ASK = new Set(["언제", "어디", "어떻게", "무엇", "뭐", "왜", "누구", "몇", "얼마"]);
const DOING = new Set(["확인", "교체", "착용", "사용", "세척", "보관", "점검", "관리", "조심", "주의"]);   // '~하다' 로 끝을 맺는 말 → 뒤로
function orderWords(picked, groups, full) {
  if (picked.length < 2) return picked;
  const pos = (q, g) => { const at = g.map((w) => mentionAt(q, w)).filter((i) => i >= 0); return at.length ? Math.min(...at) : -1; };
  const fullSet = new Set(full);
  const score = picked.map(() => 0);
  for (const q of examples) {
    const p = groups.map((g) => pos(q, g)), wgt = fullSet.has(q) ? 10 : 1;
    for (let a = 0; a < picked.length; a++) for (let b = 0; b < picked.length; b++)
      if (a !== b && p[a] >= 0 && p[b] >= 0 && p[a] < p[b]) score[a] += wgt;   // a 가 b 보다 먼저 나오면 a 점수
  }
  const kind = (w) => (ASK.has(w) ? 1 : w.endsWith("다") || DOING.has(w) ? 2 : 0);
  return picked.map((w, i) => [w, i]).sort(([wa, a], [wb, b]) => score[b] - score[a] || kind(wa) - kind(wb) || a - b).map(([w]) => w);
}
/** 질문 블럭 글자 폭 (화면 글꼴 그대로 잼) */
let measureCtx = null;
function textWidth(t) {
  measureCtx ??= document.createElement("canvas").getContext("2d");
  measureCtx.font = getComputedStyle($("suggest")).font;
  return measureCtx.measureText(t).width;
}
// 질문 블럭이 생기거나 바뀔 때 (롤러블처럼). 순서대로:
//  ① 없어질 블럭이 먼저 제자리에서 사라짐 → ② 남는 블럭이 새 자리로 미끄러지며 빈자리를 채우거나 새 블럭 자리를 비움
//     (칸 높이도 함께 늘거나 줄음) → ③ 비워 둔 자리에 새 블럭이 나타남. 도중에 또 바뀌면 지금 모습에서 새로 시작
let rollAnim = null, rollSeq = 0;
const ROLL = { duration: 500, easing: "cubic-bezier(.3,.7,.2,1)" };
const QROLL = { ...ROLL, duration: 300 };   // 질문 칸·질문 블럭은 더 빠르게 (0.5초 → 0.3초)
const FADE = { duration: 120, easing: "ease-out" };
const animDone = (a) => a.finished.catch(() => null);
async function rollTo(el, html) {
  if (el.dataset.html === html) return;
  el.dataset.html = html;
  const seq = ++rollSeq;
  for (const a of el.getAnimations({ subtree: true })) a.cancel();
  if (rollAnim) { rollAnim.onfinish = null; rollAnim.cancel(); rollAnim = null; }
  el.style.overflow = "hidden";
  if (!html) {   // 다 풀면: 질문 블럭을 그대로 둔 채 위로 말려 들어간 뒤 비움
    const from = el.getBoundingClientRect().height;
    rollAnim = el.animate([{ height: `${from}px` }, { height: "0px" }], { ...QROLL, fill: "forwards" });
    rollAnim.onfinish = () => { el.innerHTML = ""; el.style.overflow = ""; rollAnim.cancel(); rollAnim = null; };
    return;
  }
  const keys = (root) => new Set([...root.querySelectorAll("[data-k]")].map((c) => c.dataset.k));
  const tpl = document.createElement("template"); tpl.innerHTML = html;
  const nextKeys = keys(tpl.content), first = !el.querySelector("[data-k]");
  // ① 없어질 블럭 먼저 사라짐
  const leaving = [...el.querySelectorAll("[data-k]")].filter((c) => !nextKeys.has(c.dataset.k));
  if (leaving.length) {
    await Promise.all(leaving.map((c) => animDone(c.animate([{ opacity: 1 }, { opacity: 0 }], { ...FADE, fill: "forwards" }))));
    if (seq !== rollSeq) return;
  }
  // ② 남는 블럭이 새 자리로 (칸 높이도 함께)
  const from = el.getBoundingClientRect().height;
  const before = new Map([...el.querySelectorAll("[data-k]")].map((c) => [c.dataset.k, c.getBoundingClientRect().top]));
  el.innerHTML = html;
  const to = el.getBoundingClientRect().height;
  const entering = [...el.querySelectorAll("[data-k]")].filter((c) => !before.has(c.dataset.k));
  const moves = [];
  rollAnim = el.animate([{ height: `${from}px` }, { height: `${to}px` }], QROLL);
  moves.push(animDone(rollAnim));
  if (!first) {
    for (const c of entering) c.style.opacity = "0";     // 새 블럭은 자리가 비워진 뒤에
    for (const c of el.querySelectorAll("[data-k]")) {
      const t0 = before.get(c.dataset.k); if (t0 == null) continue;
      const dy = t0 - c.getBoundingClientRect().top;
      if (Math.abs(dy) >= 1) moves.push(animDone(c.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], QROLL)));
    }
  }
  await Promise.all(moves);
  if (seq !== rollSeq) return;
  el.style.overflow = ""; rollAnim = null;
  // ③ 비워 둔 자리에 새 블럭이 나타남
  if (!first) for (const c of entering) { c.style.opacity = ""; c.animate([{ opacity: 0 }, { opacity: 1 }], FADE); }
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
let panelOpen = false, pop = { s: 1, sv: 0, o: 1, ov: 0, ts: 1, to: 1, raf: 0, last: 0 };
// 팝업 자리·크기: 입력창 아래 12px, 가운데. 가로 길이는 카메라 칸에 맞춰 (입력창보다 넓지 않게)
let panelGap = null;   // 입력창 아랫면 ~ 팝업 윗면
function placePanel(p = $("panel")) {   // SOS 팝업(sos.js)도 같은 자리·폭으로 씀
  const f = $("form");
  const par = p.offsetParent || document.body, pr = par.getBoundingClientRect(), fr = f.getBoundingClientRect();
  const row = ($("qrow") || f).getBoundingClientRect();                  // 가로: 입력창+SOS 덩어리 가운데 (시험 페이지는 입력창)
  // 위쪽: 예시 질문 블럭 아래, 겹치지 않게. 예시 질문~팝업 간격 = 입력창~예시 질문 간격.
  //  입력창 아랫면에서 얼마나 떨어지는지 기억해 두어, 나중에 예시 질문이 모두 사라져도 자리는 그대로
  const ex = [...document.querySelectorAll("#examples button")].filter(b => b.offsetParent).map(b => b.getBoundingClientRect());
  if (ex.length) {
    const t = Math.min(...ex.map(r => r.top)), b = Math.max(...ex.map(r => r.bottom)), gap = t - fr.bottom;
    panelGap = b + gap - fr.bottom;
  }
  p.style.top = `${fr.bottom + (panelGap ?? 12) - pr.top}px`;
  const cam = fitCam(row, p);
  const w = Math.min(row.width, cam + 24);                               // 팝업도 카메라·단어·질문 칸에 맞춰 같이 줄임 (양옆 여백 12px)
  p.style.width = `${w}px`;
  p.style.left = `${row.left + (row.width - w) / 2 - pr.left}px`;        // 입력창(+SOS) 아래 가운데
}
// 카메라 칸·단어 칸·질문 칸 가로 길이(= 팝업 안쪽 폭): 나올 수 있는 질문 중 가장 긴 것이 줄바꿈 없이 들어가는 폭 (입력창보다 넓지 않게)
function fitCam(fr, p = $("panel")) {
  const full = fr.width - 24;
  const longest = Math.max(0, ...examples.map(textWidth));
  const cam = Math.round(Math.min(full, Math.ceil(longest) + 20 + 16 + 2));   // 글자 + 블럭 안 여백(좌우 10px) + 칸 안 여백(좌우 8px)
  // 휴대폰처럼 화면이 좁아 그 폭이 안 나오면, 질문 글자를 가장 긴 질문이 한 줄에 들어가는 크기로 줄임
  const base = parseFloat(getComputedStyle($("suggest")).fontSize) || 14;
  if (longest && cam < Math.ceil(longest) + 38) p.style.setProperty("--qfs", `${Math.floor(base * (cam - 38) / longest * 10) / 10}px`);
  else p.style.removeProperty("--qfs");
  p.style.setProperty("--cam", `${cam}px`);
  return cam;
}
addEventListener("resize", () => { if (!$("panel").hidden) placePanel(); });
// 나오고 들어가는 점: 카메라 버튼 바로 아래, 입력창 아랫면 (팝업이 입력창 안으로 겹쳐 들어가지 않게)
function popOrigin() {
  const p = $("panel"), f = $("form").getBoundingClientRect(), b = $("camBtn").getBoundingClientRect();
  const t = p.style.transform; p.style.transform = "";            // 크기가 바뀌는 중이어도 원래 크기로 재기
  const r = p.getBoundingClientRect(); p.style.transform = t;
  return `${b.left + b.width / 2 - r.left}px ${f.bottom - r.top}px`;
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
    if (!pop.to) {
      $("panel").hidden = true; document.body.classList.remove("sign-open"); document.body.style.minHeight = "";
    }
    return;
  }
  popRender();
  pop.raf = requestAnimationFrame(popStep);
}
function popTo(open) {
  const el = $("panel");
  if (open && el.hidden) { el.hidden = false; placePanel(); pop.s = POP_SCALE.from; pop.o = 0; pop.sv = pop.ov = 0; }   // 완전히 사라진 뒤 다시 열 때만 50%에서 시작
  pop.ts = open ? 1 : POP_SCALE.to; pop.to = open ? 1 : 0;
  el.style.transformOrigin = popOrigin();
  if (!pop.raf) { pop.last = performance.now(); pop.raf = requestAnimationFrame(popStep); }   // 움직이는 중이면 지금 값·속도 그대로 목표만 바꿈
}
// 화면 스크롤도 팝업과 같은 스프링으로 직접 움직임. 브라우저의 부드러운 스크롤(behavior: "smooth")은
//  첫 화면처럼 스크롤 잠금을 막 푼 직후·멀리 내릴 때 끊겨 보여서 쓰지 않음
let scrollAnim = 0;
function springScroll(to) {
  cancelAnimationFrame(scrollAnim);
  const from = scrollY, d = to - from, w = (2 * Math.PI) / SPRING.response, t0 = performance.now();
  if (Math.abs(d) < 1) return;
  const step = (t) => {
    const x = (t - t0) / 1000, k = (1 + w * x) * Math.exp(-w * x);   // 임계 감쇠 스프링: 1 → 0
    window.scrollTo(0, to - d * k);
    scrollAnim = Math.abs(d * k) < 0.5 ? 0 : requestAnimationFrame(step);
    if (!scrollAnim) window.scrollTo(0, to);
  };
  scrollAnim = requestAnimationFrame(step);
}
for (const ev of ["wheel", "touchstart", "keydown"]) addEventListener(ev, () => { cancelAnimationFrame(scrollAnim); scrollAnim = 0; }, { passive: true });   // 사용자가 직접 스크롤하면 멈춤
let scrollBack = null;   // 팝업을 열 때 화면을 입력창이 위로 오게 내렸다면, 닫을 때 원래 자리로
function openPanel(pre = null) {
  // 팝업이 다 닫힌 뒤 다시 열면 처음 상태로 (닫히는 도중에 다시 누르면 하던 그대로)
  const fresh = $("panel").hidden;
  if (fresh) resetWords();
  panelOpen = true; $("camBtn").classList.add("on");
  dispatchEvent(new Event("sign-open"));      // 본 화면: 마이크로 듣는 중이면 팝업이 뜨기 시작하는 순간 같이 끔
  document.body.classList.add("sign-open");   // 본 화면 첫 화면은 스크롤을 막아 두므로, 팝업이 떠 있는 동안은 풀어 줌
  popTo(true);
  // 팝업 전체(카메라~질문)가 한 화면에 보이게: 입력창이 화면 위쪽(16px)에 오도록 부드럽게 내림
  //  팝업은 커지는 중이라 아직 페이지가 짧으므로, 내릴 수 있게 페이지 최소 높이를 잡아 둠 (닫히면 풂)
  const top = $("form").getBoundingClientRect().top;
  if (Math.abs(top - 16) > 2) {
    scrollBack ??= scrollY;
    document.body.style.minHeight = `${Math.ceil(scrollY + top - 16 + innerHeight)}px`;
    requestAnimationFrame(() => { if (panelOpen) springScroll(scrollY + $("form").getBoundingClientRect().top - 16); });
  }
  if (fresh) measureNotice();
  startCamera(pre);
}
function closePanel() {
  if (stream) stopCamera();
  if (!panelOpen) return;
  panelOpen = false; $("camBtn").classList.remove("on");
  popTo(false);
  if (scrollBack != null) { springScroll(scrollBack); scrollBack = null; }
}
// 카메라를 쓸 수 없는 게 미리 보이면(카메라 없음·권한 거절) 팝업을 열지 않음. 모르면 열고, 켜기에 실패하면 바로 닫음
async function cameraUsable() {
  if (!navigator.mediaDevices?.getUserMedia) return false;
  try { if ((await navigator.permissions?.query({ name: "camera" }))?.state === "denied") return false; } catch {}
  try {
    const ds = await navigator.mediaDevices.enumerateDevices();
    if (ds.length && !ds.some((d) => d.kind === "videoinput")) return false;
  } catch {}
  return true;
}
// 이미 허용한 기기면 바로 팝업. 아직이면 먼저 카메라 권한을 묻고, '허용'을 누른 뒤에 팝업을 엶 (거절하면 열지 않음)
let asking = false;
$("camBtn").onclick = async () => {
  if (panelOpen) return closePanel();
  if (asking || !(await cameraUsable())) return;
  let st = null;
  try { st = (await navigator.permissions?.query({ name: "camera" }))?.state; } catch {}
  if (st === "granted") return openPanel();
  asking = true;
  try {
    const s = await navigator.mediaDevices.getUserMedia(VIDEO);   // 여기서 브라우저가 허용 여부를 물음
    if (panelOpen) { s.getTracks().forEach((t) => t.stop()); return; }
    openPanel(s);
  } catch (e) { console.error(e); }                              // 거절·실패: 팝업을 열지 않음
  finally { asking = false; }
};
// 팝업이 떠 있을 때 팝업 밖을 누르면 그 순간 팝업이 닫히기 시작함.
//  누른 곳이 버튼(예시 질문 등)이면 그 버튼도 그 순간 그대로 눌림 (예시 질문이 바로 입력창에 들어감), 빈 배경이면 닫기만
document.addEventListener("click", (e) => {
  if (!panelOpen || $("panel").contains(e.target) || $("camBtn").contains(e.target)) return;
  closePanel();
  if (!e.target.closest?.("button, a, [role=button]")) { e.preventDefault(); e.stopPropagation(); }
}, true);

// 단어 칸(안내 문구 포함): 처음 나타날 때 질문 칸처럼 아래로 늘어나며 내려옴
let wordsAnim = null;
function showWords() {
  const el = $("wordsWrap");
  if (!el.hidden) return;
  el.hidden = false;
  const h = el.getBoundingClientRect().height;
  el.style.overflow = "hidden";
  wordsAnim = el.animate([{ height: "0px" }, { height: `${h}px` }], ROLL);
  wordsAnim.onfinish = () => { el.style.overflow = ""; wordsAnim = null; };
}
/** 처음 상태로: 알아들은 낱말·고른 낱말·질문 블럭을 모두 비움 (카메라 칸만 남음) */
function resetWords() {
  items.length = 0; selOrder = 0; win.lastCommitted = "";
  const box = $("suggest"); box.innerHTML = ""; box.dataset.html = "";
  if (wordsAnim) { wordsAnim.cancel(); wordsAnim = null; }
  $("wordsWrap").hidden = true;   // 팝업이 열리기 전(안 보일 때)에만 비우므로 움직임 없이 숨김
  renderWords();
}
renderWords();
// 안내 문구 글자 위·아래 빈칸 재기: 줄 높이 안에서 실제 글자(잉크)가 차지하지 않는 위·아래 높이 → CSS --nt·--nb
//  기준선 위치는 화면에서 직접 재고(높이 0 칸을 글자 줄에 붙여 봄), 글자 높이는 같은 글꼴로 그려서 잼
function measureNotice() {
  const el = document.querySelector("#panel .notice"); if (!el || !el.getClientRects().length) return;
  const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
  const probe = document.createElement("span"); probe.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
  el.appendChild(probe);
  const r = el.getBoundingClientRect(), base = probe.getBoundingClientRect().bottom - r.top;
  probe.remove();
  const g = document.createElement("canvas").getContext("2d"); g.font = `${cs.fontWeight} ${fs}px ${cs.fontFamily}`;
  const m = g.measureText(el.textContent);
  const top = Math.max(0, base - m.actualBoundingBoxAscent), bottom = Math.max(0, r.height - (base + m.actualBoundingBoxDescent));
  document.documentElement.style.setProperty("--nt", `${top.toFixed(2)}px`);
  document.documentElement.style.setProperty("--nb", `${bottom.toFixed(2)}px`);
}
document.fonts?.ready.then(measureNotice);
prepare().catch(() => {});   // 페이지를 열면 바로 모델 준비 (카메라 권한은 팝업을 열 때 물음)
window.__sign = { opts, items, renderWords, draw, place: placePanel, springScroll, closePanel, isOpen: () => panelOpen, docWords: (ws) => docWords(ws, domain), rec, addItem,
  classify: (f) => classify(rec, f, opts, domainIdx, protos) };
