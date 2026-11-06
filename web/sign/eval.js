// 정확도 평가: 녹화한 관절 점 파일(index.html '정확도 평가용 녹화')을 설정별로 다시 돌려 비교. 모델은 브라우저에서 그대로 실행.
import { DEFAULTS, FrameCleaner, Recognizer, Segmenter, WindowCommitter, base, classify, docWords, frameToFeatures,
  rankQuestions, rerank } from "./core.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const rec = new Recognizer();
let domain = null, domainIdx = null, protos = [];

// 비교할 설정: 모두 켠 것, 예전 방식, 하나씩 끈 것
const CONFIGS = [
  ["새 방식 (모두 켬)", {}],
  ["예전 방식 (팀원 그대로)", { segment: "window", calibrate: false, smooth: false, rerank: false, custom: false, domain: false }],
  ["예전 방식 + 산업안전 단어만", { segment: "window", calibrate: false, smooth: false, rerank: false, custom: false }],
  ["낱말 끊기만 예전", { segment: "window" }],
  ["산업안전 단어만 끔", { domain: false }],
  ["확신 판단 끔", { calibrate: false }],
  ["떨림 줄이기 끔", { smooth: false }],
  ["깊이(z) 끔", { useZ: false }],
  ["후보 다시 고르기 끔", { rerank: false }],
  ["새 낱말 끔", { custom: false }],
];

async function init() {
  domain = await (await fetch("./domain.json")).json();
  $("msg").textContent = "모델 불러오는 중…";
  await rec.load("./", (g, t) => t && ($("msg").textContent = `모델 불러오는 중… ${Math.round(100 * g / t)}%`));
  const s = new Set(domain.labels);
  domainIdx = rec.labels.map((l, i) => (s.has(l) ? i : -1)).filter((i) => i >= 0);
  $("msg").textContent = "준비 완료";
  $("run").disabled = false;
}

const toFrame = (f) => ({ pose: f.pose.map(([x, y, z, v]) => ({ x, y, z, visibility: v })), leftHand: f.lh ? f.lh.map(([x, y, z]) => ({ x, y, z })) : [],
  rightHand: f.rh ? f.rh.map(([x, y, z]) => ({ x, y, z })) : [] });

/** 녹화 하나를 설정 하나로 → 알아들은 낱말들 */
async function runOne(recording, o) {
  const cleaner = new FrameCleaner(), seg = new Segmenter(), win = new WindowCommitter(), items = [];
  const cls = (fs) => classify(rec, fs, o, domainIdx, protos);
  for (const f of recording.frames) {
    const raw = toFrame(f), frame = o.smooth ? cleaner.clean(raw, f.t) : raw, feat = frameToFeatures(frame, o.useZ);
    if (o.segment === "motion") { const s = seg.push(frame, feat, f.t); if (s) items.push(await cls(s.frames)); }
    else { const r = await win.push(feat, f.t, cls); if (r) items.push(r); }
  }
  // 끝까지 손을 들고 있었으면 마지막 낱말도
  if (o.segment === "motion" && seg.cur && seg.cur.frames.length >= 4) items.push(await cls(seg.cur.frames));
  const its = items.map((r) => ({ label: r.label, cands: r.cands, locked: false }));
  const ch = o.rerank && its.length > 1 ? rerank(its, domain.questions, domain) : its.map((i) => i.label);
  return ch.map((l) => (domain.words[l] || base(l)));
}

function score(words, label) {
  const want = (label.words || "").split(/\s+/).filter(Boolean);
  const got = new Set(words);
  const hit = want.filter((w) => got.has(w)).length;
  const qs = rankQuestions(docWords(words, domain), domain.questions, 3).map(([, q]) => q);
  return { wordRecall: want.length ? hit / want.length : null, wordPrec: words.length && want.length ? words.filter((w) => want.includes(w)).length / words.length : null,
    q1: label.question ? qs[0] === label.question : null, q3: label.question ? qs.includes(label.question) : null, qs };
}

$("run").onclick = async () => {
  const files = [...$("files").files];
  if (!files.length) { $("msg").textContent = "녹화 파일을 고르세요"; return; }
  const recs = await Promise.all(files.map(async (f) => ({ name: f.name, data: JSON.parse(await f.text()) })));
  $("run").disabled = true;
  const rows = [], detail = [];
  for (const [name, over] of CONFIGS) {
    const o = { ...DEFAULTS, ...over }, agg = { wr: [], wp: [], q1: [], q3: [] };
    for (const r of recs) {
      $("msg").textContent = `${name} · ${r.name}`;
      const words = await runOne(r.data, o), s = score(words, r.data.label || {});
      if (s.wordRecall !== null) { agg.wr.push(s.wordRecall); agg.wp.push(s.wordPrec ?? 0); }
      if (s.q1 !== null) { agg.q1.push(s.q1 ? 1 : 0); agg.q3.push(s.q3 ? 1 : 0); }
      detail.push({ name, file: r.name, label: r.data.label, words, top: s.qs[0] });
    }
    const pct = (a) => (a.length ? `${Math.round(100 * a.reduce((x, y) => x + y, 0) / a.length)}%` : "-");
    rows.push(`<tr><td>${esc(name)}</td><td>${pct(agg.wr)}</td><td>${pct(agg.wp)}</td><td>${pct(agg.q1)}</td><td>${pct(agg.q3)}</td></tr>`);
  }
  $("result").innerHTML = `<table><tr><th>설정</th><th>낱말 맞음 (찾은 비율)</th><th>낱말 정확 (맞은 비율)</th><th>질문 1순위</th><th>질문 3순위 안</th></tr>${rows.join("")}</table>`;
  $("detail").innerHTML = `<table><tr><th>설정</th><th>파일</th><th>한 질문 / 낱말</th><th>알아들은 낱말</th><th>찾은 질문</th></tr>${detail.map((d) =>
    `<tr><td>${esc(d.name)}</td><td>${esc(d.file)}</td><td>${esc(d.label?.question || "")}<br><small>${esc(d.label?.words || "")}</small></td><td>${esc(d.words.join(" "))}</td><td>${esc(d.top || "")}</td></tr>`).join("")}</table>`;
  $("msg").textContent = `녹화 ${recs.length}개 × 설정 ${CONFIGS.length}개 완료`;
  $("run").disabled = false;
};

init().catch((e) => { $("msg").textContent = `불러오지 못했습니다: ${e.message || e}`; });
window.__eval = { runOne, CONFIGS, ready: () => !!rec.session };
