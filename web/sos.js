// SOS: 버튼을 누르면 위치 권한을 묻고, 허락하면 팝업에 지도(위)와 가장 가까운 응급실 정보(아래).
// 팝업 틀·자리·폭·움직임은 카메라 팝업과 같음 (sign/popup.css, sign/app.js 의 place·springScroll).
// 미리 할 수 있는 일(카카오 지도 불러오기, 위치를 이미 허락한 기기면 위치·가까운 응급실)은 첫 로딩 화면에서 → 'sos-ready'
const $ = (id) => document.getElementById(id);
const API = new URL("./", import.meta.url).href;
const SPRING = { response: 0.35, damping: 1 }, POP_SCALE = { from: 0.5, to: 0.2 };   // 카메라 팝업과 같은 값

let cfg = null, kakaoReady = null, pos = null, posAt = 0, result = null, resultFor = null, map = null, layers = [];
let open = false, scrollBack = null;
const pop = { s: 1, sv: 0, o: 1, ov: 0, ts: 1, to: 1, raf: 0, last: 0 };

// ---------- 미리 준비 (첫 로딩 화면) ----------
function loadKakao(key) {
  return new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false`;
    s.onload = () => window.kakao.maps.load(res);
    s.onerror = rej;
    document.head.append(s);
  });
}
// 위치: 빠른 위치(와이파이·기지국)로 먼저. 정밀 GPS 는 몇~수십 초 걸려서 쓰지 않음 (응급실 찾기에는 충분)
function getPos(timeout = 8000, precise = false, fresh = false) {
  return new Promise((res, rej) => {
    if (!navigator.geolocation) return rej(new Error("no geolocation"));
    navigator.geolocation.getCurrentPosition((p) => {
      pos = { lat: p.coords.latitude, lng: p.coords.longitude }; posAt = Date.now(); res(pos);
    }, rej, { enableHighAccuracy: precise, timeout, maximumAge: precise || fresh ? 0 : 300000 });
  });
}
async function fetchNearest(p, fresh = false) {   // fresh: 저장해 둔 결과를 쓰지 않고 새로 (서버 1분 캐시도 건너뜀)
  const key = `${p.lat.toFixed(3)},${p.lng.toFixed(3)}`;
  if (!fresh && result && resultFor === key) return result;
  const r = await fetch(`${API}sos/nearest?lat=${p.lat}&lng=${p.lng}${fresh ? "&fresh=1" : ""}`);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || r.status);
  result = d; resultFor = key;
  return d;
}
(async () => {
  try {
    cfg = await (await fetch(`${API}sos/config`)).json();
    if (cfg.kakao_js) { kakaoReady = loadKakao(cfg.kakao_js); await kakaoReady.catch(() => { kakaoReady = null; }); }
    // 위치를 이미 허락해 둔 기기면 위치와 가까운 응급실까지 미리 (처음이면 권한은 버튼을 누를 때 물음)
    const st = await navigator.permissions?.query({ name: "geolocation" }).catch(() => null);
    if (st && st.state === "granted") await getPos(6000).then(fetchNearest).catch(() => {});
  } catch { /* 준비 실패: 버튼을 누를 때 다시 */ }
  dispatchEvent(new Event("sos-ready"));
})();

// SOS 원 지름 = 입력창 높이
const fitBtn = () => { const h = $("form").getBoundingClientRect().height; if (h) { $("sosBtn").style.width = $("sosBtn").style.height = `${h}px`; } };
fitBtn();
if (window.ResizeObserver) new ResizeObserver(fitBtn).observe($("form"));

// ---------- 지도 · 응급실 정보 ----------
// 내 위치·도착 위치 표시: 빨간 핀 (경로 선과 같은 색)
// 현위치·도착 표시: 말풍선 핀에 흰 글씨, 테두리 없음 (현위치 파랑, 도착 빨강)
const PIN_COLOR = { 현위치: "#007aff", 도착: "#ff3b30" };
const tagPin = (t) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(   // 물방울 모양 핀 안에 '현위치'·'도착' (세 글자는 핀 안에 들어가게 조금 작게)
  `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="54" viewBox="0 0 44 54"><path d="M22 53C22 53 2 32.5 2 21a20 20 0 0 1 40 0c0 11.5-20 32-20 32Z" fill="${PIN_COLOR[t]}"/><text x="22" y="25.5" text-anchor="middle" font-family="-apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', 'Malgun Gothic', sans-serif" font-size="${t.length > 2 ? 11.5 : 13}" font-weight="700" fill="#ffffff">${t}</text></svg>`);
const pins = {};
const pin = (K, t) => (pins[t] ??= new K.MarkerImage(tagPin(t), new K.Size(44, 54), { offset: new K.Point(22, 53) }));
// 첫 로딩 때 지도 준비가 실패했으면(서버가 다시 시작하는 중 등) 버튼을 누를 때 다시 준비
let mapLoading = null;
function ensureMap() {
  if (kakaoReady && window.kakao?.maps) return Promise.resolve();
  return (mapLoading ??= (async () => {
    if (!cfg || !cfg.kakao_js) cfg = await (await fetch(`${API}sos/config`)).json();
    if (cfg.kakao_js) { kakaoReady = loadKakao(cfg.kakao_js); await kakaoReady; }
  })().catch(() => { kakaoReady = null; }).finally(() => { mapLoading = null; }));
}
function redrawMap() {
  if (!map || !lastPos) return;
  map.relayout();
  drawMap(lastPos, shown ? (shown.ers || [])[sel] || shown : null);
}
function drawMap(p, d) {
  if (!kakaoReady || !window.kakao?.maps) return;
  const K = window.kakao.maps, here = new K.LatLng(p.lat, p.lng);
  if (!map) map = new K.Map($("sosMap"), { center: here, level: 4 });
  else map.relayout();
  layers.forEach((l) => l.setMap(null)); layers = [];
  const er = d && d.er;
  const path = er ? (d.route && d.route.path || []).map(([la, lo]) => new K.LatLng(la, lo)) : [];
  // 경로가 있으면 출발·도착 표시를 경로의 처음·끝에 둠 (경로는 길 위에서 시작·끝나서, 내 위치·병원 위치에 두면 선과 표시 사이가 뜸)
  //  경로가 없으면(아직 안 왔거나 못 구함) 내 위치·병원 위치에
  const from = path.length ? path[0] : here, to = path.length ? path[path.length - 1] : er && new K.LatLng(er.lat, er.lng);
  // 지도 범위를 먼저 옮긴 뒤 선·표시를 그림. 선을 먼저 그리고 범위를 옮기면(다른 병원을 고를 때) 선이 예전 화면 범위에서 잘린 채 남음
  const bounds = new K.LatLngBounds(); bounds.extend(from);
  path.forEach((x) => bounds.extend(x));
  if (to) { bounds.extend(to); map.setBounds(bounds, 24, 24, 24, 24); } else map.setCenter(here);
  if (path.length) {
    const line = new K.Polyline({ path, map, strokeWeight: 6, strokeColor: "#007aff", strokeOpacity: 1, strokeStyle: "solid" });
    layers.push(line);
    // 지도가 다 움직인 뒤 선을 한 번 더 그려 잘린 부분이 없게
    const again = () => { K.event.removeListener(map, "idle", again); if (layers.includes(line)) { line.setMap(null); line.setMap(map); } };
    K.event.addListener(map, "idle", again);
  }
  layers.push(new K.Marker({ position: from, map, image: pin(K, "현위치"), zIndex: 2 }));
  if (to) layers.push(new K.Marker({ position: to, map, title: er.name, image: pin(K, "도착"), zIndex: 2 }));
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let shown = null, sel = 0, lastPos = null;   // 지금 보이는 결과, 지도에 경로를 그린 병원(0: 1순위, 1: 2순위)
// 병원이 응급실 병상 정보를 올린 때 (공공데이터 hvidate, 예: 20261109175700) → ['11월 9일', '17:57']
function when(v) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})/.exec(v || "");
  if (!m) return "";
  return [`${+m[2]}월 ${+m[3]}일`, `${m[4]}:${m[5]}`];   // 24시간제
}
function erBox(x, i) {
  const er = x.er, rt = x.route;
  const km = ((rt && rt.distance != null ? rt.distance / 1000 : er.km) || 0).toFixed(1);
  const bed = er.beds > 0 ? `응급실 남은 병상 ${er.beds}` : "응급실 남은 병상 없음";   // 병상 정보가 없어도 '없음'으로
  const tel = er.er_tel || er.tel;
  const chip = (t, c) => `<span class="chip ${c}">${esc(t)}</span>`;
  // 길찾기: 카카오맵에서 출발 = 지금 위치, 도착 = 이 병원 (휴대폰은 카카오맵 앱으로 이어짐)
  const enc = encodeURIComponent, here = lastPos ? `from/${enc("현재 위치")},${lastPos.lat},${lastPos.lng}/` : "";
  const nav = `<a class="chip" href="https://map.kakao.com/link/${here}to/${enc(er.name)},${er.lat},${er.lng}" target="_blank" rel="noopener">길찾기</a>`;
  // 줄 구성: [자동차 n분] [n km] [길찾기] / [응급의료기관 등급] [전화] / [응급실 병상] [병원이 병상 정보를 올린 때]. 줄 안의 칸은 같은 너비, 없는 칸은 빼고 남은 칸이 나눠 씀
  const row = (...xs) => { xs = xs.filter(Boolean); return xs.map((x) => x.replace('class="chip', `class="chip c${xs.length}`)).join(""); };
  const upd = when(er.updated);
  const cells = [
    row(rt && rt.duration != null ? chip(`자동차 ${Math.max(1, Math.round(rt.duration / 60))}분`, "") : "", chip(`${km}km`, ""), nav),
    row(er.grade ? chip(er.grade, "") : "", tel ? `<a class="chip" href="tel:${esc(tel.replace(/[^0-9+]/g, ""))}">${esc(tel)}</a>` : ""),
    row(chip(bed, ""), upd ? `<span class="chip">${upd[0]} <span class="nb">${upd[1]} 기준</span></span>` : ""),   // 줄이 넘치면 날짜 / 시각 기준 으로 나뉨
  ];
  // 병원이 올린 진료 제한 메시지 (예: '정형외과 전문의 부재로 수지접합 불가'): 블럭 아래 한 줄씩
  const msg = (er.msgs || []).map((m) => `<span class="chip msg">${esc(m)}</span>`).join("");
  // 항목 끝 괄호 부분(예: '(1500g 이상, 30주 이상)')은 한 덩어리: 줄이 넘치면 괄호 부분이 통째로 다음 줄로 (브라우저마다 '(' 뒤에서 끊는 것 방지)
  const item = (x) => { const m = /^(.*?)\s*(\([^()]*\))$/.exec(x); return m ? `${esc(m[1])} <span class="paren">${esc(m[2])}</span>` : esc(x); };
  // 중증질환 수용 정보 (칸 맨 아래): 누르면 같은 박스 안에서 펼쳐져 가능·불가 항목을 모두 보여 줌 (다시 누르면 접힘)
  const sv = er.serious, sevList = (t, xs) => `<p class="sub">${t}</p><div class="row">${xs.length ? xs.map((x) => `<span class="chip">${item(x)}</span>`).join("") : `<span class="chip">없음</span>`}</div>`;
  const sev = sv && (sv["가능"].length || sv["불가"].length)
    ? `<div class="sevbox"><button type="button" class="chip tog" data-tog aria-expanded="false">중증질환 수용 정보</button><div class="sev" hidden>${sevList("가능", sv["가능"])}${sevList("불가", sv["불가"])}</div></div>` : "";
  return `<section class="box info${i === sel ? " sel" : ""}" data-i="${i}"><p class="name">${esc(er.name)}</p><div class="grid">${cells.join("")}${msg}${sev}</div></section>`;
}
function showInfo(d, err) {
  const wrap = $("sosInfo");
  const list = d ? (d.ers && d.ers.length ? d.ers : d.er ? [{ er: d.er, route: d.route }] : []) : [];
  if (err || !list.length) { wrap.innerHTML = ""; wrap.hidden = true; return; }   // 못 불러오면 정보 칸 없이 지도만
  if (shown !== d) { shown = d; sel = 0; }
  const first = wrap.hidden;
  wrap.innerHTML = list.map(erBox).join("");
  wrap.hidden = false;
  if (first) roll(wrap);
}
// 응급실 정보 칸: 처음 나타날 때 카메라 팝업 단어 칸처럼 아래로 늘어나며 내려옴 (0.5초, 같은 곡선). 지도 칸과의 12px 간격도 같이 벌어짐
const ROLL = { duration: 500, easing: "cubic-bezier(.3,.7,.2,1)" };
function roll(el) {
  const h = el.getBoundingClientRect().height, m = getComputedStyle(el).marginTop;
  el.style.overflow = "hidden";
  el.animate([{ height: "0px", marginTop: "0px" }, { height: `${h}px`, marginTop: m }], ROLL).onfinish = () => { el.style.overflow = ""; };
}
// 두 줄 이상으로 넘친 블럭은 박스 너비를 가장 긴 줄에 맞추고, 그 줄에는 그 블럭만 둠 (그냥 두면 줄이 바뀌어도 박스는 한 줄 전체 너비로 남음)
function fitChips(root) {
  for (const el of root.querySelectorAll(".chip")) {
    el.style.width = el.style.marginInline = "";
    const rg = document.createRange(); rg.selectNodeContents(el);
    const lines = [...rg.getClientRects()];
    if (new Set(lines.map((x) => Math.round(x.top))).size < 2) continue;
    const cs = getComputedStyle(el), pad = cs.boxSizing === "border-box" ? parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) : 0;
    const w = Math.ceil(Math.max(...lines.map((x) => x.width))) + pad + 1;
    el.style.width = `${w}px`;
    el.style.marginInline = `calc((100% - ${w}px) / 2)`;   // 양옆 여백으로 줄을 채워, 두 줄 블럭이 있는 줄에는 그 블럭만

  }
}
// 응급실 칸을 누르면 위 지도의 경로가 그 병원으로 (1순위 ↔ 2순위)
$("sosInfo").addEventListener("click", (e) => {
  const t = e.target.closest?.("[data-tog]");
  if (t) {                                       // 중증질환 수용 정보 펼치기·접기 (지도 경로는 바꾸지 않음)
    // 응급실 정보 칸이 처음 나올 때와 같은 효과: 펼칠 때 아래로 늘어나고, 접을 때 위로 줄어든 뒤 숨김
    const box = t.nextElementSibling, open1 = box.hidden || box.dataset.closing === "1";
    t.setAttribute("aria-expanded", String(open1));
    box.getAnimations().forEach((a) => a.cancel());
    box.hidden = false; box.dataset.closing = "";
    fitChips(box);
    const cs = getComputedStyle(box), h = box.getBoundingClientRect().height;
    const over = t.parentElement.getBoundingClientRect().bottom + 24 - innerHeight;
    if (open1 && over > 0) scrollInto(over);   // 펼친 목록이 화면 아래로 넘치면 그만큼 같이 내려 줌
    const full = { height: `${h}px`, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom }, zero = { height: "0px", paddingTop: "0px", paddingBottom: "0px" };
    box.style.overflow = "hidden";
    if (!open1) { box.dataset.closing = "1"; shrinkPage(h); }
    box.animate(open1 ? [zero, full] : [full, zero], ROLL).onfinish = () => {
      box.style.overflow = "";
      if (!open1) { box.hidden = true; box.dataset.closing = ""; document.documentElement.style.minHeight = baseMin; }
    };
    return;
  }
  const b = e.target.closest?.(".info[data-i]");
  if (!b || !shown || !lastPos) return;
  const i = +b.dataset.i, list = shown.ers || [];
  if (!list[i] || i === sel) return;
  sel = i;
  for (const x of $("sosInfo").querySelectorAll(".info[data-i]")) x.classList.toggle("sel", +x.dataset.i === sel);   // 도착 병원 칸 빨강
  drawMap(lastPos, list[i]);
  // 지도가 화면에서 다 안 보이면, 바뀐 경로를 바로 볼 수 있게 팝업을 열 때 자리(입력창이 화면 위쪽 16px)로 올림
  const m = $("sosMap").getBoundingClientRect();
  if (m.top < 0 || m.bottom > innerHeight) scrollInto($("form").getBoundingClientRect().top - 16);
});
let baseMin = "";   // 팝업을 열 때 잡아 둔 페이지 최소 높이 (토글로 늘렸다가 접으면 여기로 되돌림)
// 화면을 by 만큼(+ 아래, - 위) 팝업과 같은 스프링으로 움직임. 아래로 내릴 자리가 모자라면 페이지 최소 높이를 늘려 둠 (닫히면 풂)
// 토글을 접어 팝업이 h 만큼 줄어들 때: 늘려 둔 페이지 높이를 되돌리고, 그 때문에 화면 아래가 비면 접히는 동안 같이 올려 줌
function shrinkPage(h) {
  const de = document.documentElement, cur = de.style.minHeight;
  de.style.minHeight = baseMin;
  const after = Math.max(parseFloat(baseMin) || 0, de.scrollHeight - h);   // 다 접힌 뒤 페이지 높이
  de.style.minHeight = cur;
  const max = Math.max(0, after - innerHeight);
  if (scrollY > max + 1) window.__sign?.springScroll?.(max);
}
function scrollInto(by) {
  if (Math.abs(by) < 1) return;
  if (by < 0 && scrollY + by < 0) by = -scrollY;
  const to = scrollY + by, need = Math.ceil(to + innerHeight);
  if (by > 0 && document.documentElement.scrollHeight < need) document.documentElement.style.minHeight = `${need}px`;
  window.__sign?.springScroll?.(to);
}
async function refresh(p) {
  if (!p || $("sosPanel").hidden) return;     // 위치가 아직이면 지도·정보는 위치가 나오면 그림 (다 닫혔으면 그리지 않음)
  const my = gen;                              // 다 닫힌 뒤 새로 열었으면 이 결과는 버림
  drawMap(p, null);                            // 먼저 내 위치만
  lastPos = p;
  // 지도가 아직 없으면 먼저 내 위치만. 카카오 지도가 끝까지 안 불러와져도 응급실 정보는 4초 뒤 보여 줌
  const mapP = Promise.race([ensureMap().then(() => { if (open && !map) drawMap(p, null); }), new Promise((r) => setTimeout(r, 4000))]);
  try { const d = await fetchNearest(p, true); await mapP; await opened(); if (my === gen) { showInfo(d); drawMap(p, (d.ers || [])[sel] || d); } }
  catch { await mapP; await opened(); if (my === gen) showInfo(null, true); }
}

// ---------- 팝업 열기·닫기 (카메라 팝업과 같은 스프링) ----------
function origin() {
  const p = $("sosPanel"), f = $("form").getBoundingClientRect(), b = $("sosBtn").getBoundingClientRect();
  const t = p.style.transform; p.style.transform = "";
  const r = p.getBoundingClientRect(); p.style.transform = t;
  return `${b.left + b.width / 2 - r.left}px ${f.bottom - r.top}px`;
}
// 팝업(지도 칸)이 다 커진 뒤에 응급실 정보 칸이 아래로 늘어나며 나오도록 기다림 (껐다 켜도 처음 열 때와 같은 순서)
let openWait = [];
const opened = () => (pop.to === 1 && !pop.raf ? Promise.resolve() : new Promise((r) => openWait.push(r)));
function render() {
  const el = $("sosPanel");
  el.style.opacity = String(Math.min(1, Math.max(0, pop.o)));
  el.style.transform = Math.abs(pop.s - 1) < 0.001 ? "" : `scale(${pop.s})`;
  el.style.pointerEvents = pop.to ? "" : "none";
}
function step(t) {
  const dt = Math.min(0.032, (t - pop.last) / 1000 || 0.016); pop.last = t;
  const w = (2 * Math.PI) / SPRING.response, k = 2 * SPRING.damping * w;
  pop.sv += (-w * w * (pop.s - pop.ts) - k * pop.sv) * dt; pop.s += pop.sv * dt;
  pop.ov += (-w * w * (pop.o - pop.to) - k * pop.ov) * dt; pop.o += pop.ov * dt;
  if (Math.abs(pop.s - pop.ts) < 0.001 && Math.abs(pop.sv) < 0.01 && Math.abs(pop.o - pop.to) < 0.001 && Math.abs(pop.ov) < 0.01) {
    pop.s = pop.ts; pop.o = pop.to; pop.sv = pop.ov = 0; pop.raf = 0; render();
    if (!pop.to) { $("sosPanel").hidden = true; document.body.classList.remove("sos-open"); document.documentElement.style.minHeight = baseMin = ""; }
    else { redrawMap(); openWait.splice(0).forEach((f) => f()); }   // 커지는 중(축소된 상태)에 그린 경로는 잘린 채 남아 끊겨 보임 → 다 커진 뒤 지도 크기를 다시 재고 다시 그림
    return;
  }
  render();
  pop.raf = requestAnimationFrame(step);
}
function popTo(show) {
  const el = $("sosPanel");
  if (show && el.hidden) {
    // 카메라 팝업과 같은 기준: 다 닫힌 뒤 다시 열면 완전히 처음부터 (정보 칸 비움, 지도 표시 지움, 위치 찾기·응급실 조회도 새로).
    //  닫히는 도중에 다시 누르면 보던 그대로
    $("sosInfo").hidden = true; $("sosInfo").innerHTML = ""; shown = null; sel = 0; lastPos = null;
    layers.forEach((l) => l.setMap(null)); layers = [];   // 지도 틀은 그대로 두고 표시만 지움 (지도를 새로 만들면 경로 선이 안 그려지는 때가 있음)
    el.hidden = false;
    window.__sign?.place?.(el);
    pop.s = POP_SCALE.from; pop.o = 0; pop.sv = pop.ov = 0;
  }
  pop.ts = show ? 1 : POP_SCALE.to; pop.to = show ? 1 : 0;
  el.style.transformOrigin = origin();
  if (!pop.raf) { pop.last = performance.now(); pop.raf = requestAnimationFrame(step); }
}
function openPanel(p) {
  open = true; shownGen = gen; $("sosBtn").classList.add("on");
  document.body.classList.add("sos-open");
  popTo(true);
  const top = $("form").getBoundingClientRect().top;   // 카메라 팝업처럼 입력창이 화면 위쪽(16px)에 오도록
  if (Math.abs(top - 16) > 2) {
    scrollBack ??= scrollY;
    document.documentElement.style.minHeight = baseMin = `${Math.ceil(scrollY + top - 16 + innerHeight)}px`;
    requestAnimationFrame(() => { if (open) window.__sign?.springScroll?.(scrollY + $("form").getBoundingClientRect().top - 16); });
  }
  refresh(p);
}
function closePanel() {
  if (!open) return;
  open = false; $("sosBtn").classList.remove("on");
  popTo(false);
  if (scrollBack != null) { window.__sign?.springScroll?.(scrollBack); scrollBack = null; }
}
let gen = 0, shownGen = 0;   // gen: 새로 열 때마다 하나씩 (예전 위치 찾기·조회 결과는 버림), shownGen: 그 회차에 팝업을 연 적 있음
$("sosBtn").onclick = async () => {
  if (open) return closePanel();
  if (!$("sosPanel").hidden) return openPanel(null);   // 닫히는 도중: 보던 그대로 다시 (찾던 위치·조회도 이어서)
  const my = ++gen;
  // 다 닫힌 뒤 새로 열면 처음부터: 저장해 둔 위치를 쓰지 않고 지금 위치를 새로 찾음
  // 위치 권한을 묻고, 허락하는 순간 팝업을 엶 (위치가 나오기를 기다리지 않음). 거절하면 열지 않음
  const st = await navigator.permissions?.query({ name: "geolocation" }).catch(() => null);
  if (my !== gen) return;
  if (st && st.state === "granted") openPanel(null);
  else if (st) st.onchange = () => { if (st.state === "granted" && !open && my === gen && shownGen !== my) openPanel(null); st.onchange = null; };
  // 위치: 빠른 위치(8초)로 먼저, 못 받으면 정밀 위치로 한 번 더 (20초). 그 사이 닫았으면 다시 열지 않음
  try {
    const p = await getPos(8000, false, true).catch(() => getPos(20000, true));
    if (my !== gen) return;
    if (shownGen !== my) openPanel(p); else refresh(p);
  } catch { if (my === gen) showInfo(null, true); }
};
// 팝업 밖의 버튼(카메라·마이크·예시 질문 등)을 누르면 닫히고 그 버튼도 그대로 눌림. 빈 배경을 누르면 닫히지 않음
document.addEventListener("click", (e) => {
  if (!open || $("sosPanel").contains(e.target) || $("sosBtn").contains(e.target)) return;
  if (e.target.closest?.("button, a, [role=button]")) closePanel();
}, true);
addEventListener("resize", () => { if (open) { window.__sign?.place?.($("sosPanel")); map?.relayout(); $("sosInfo").querySelectorAll(".sev:not([hidden])").forEach(fitChips); } });
