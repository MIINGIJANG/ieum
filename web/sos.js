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
    // 카카오 지도는 경로 선 그림판(SVG) 크기를 지도 칸의 '보이는' 크기로 잼. 팝업이 커지거나 줄어드는 중(scale)에 재면
    //  그림판이 작게 남아 선이 잘리거나 안 보임 → 지도가 다 움직인 뒤, 다음 화면, 0.4초 뒤에 크기를 다시 재고 선을 다시 그림
    const again = () => {
      if (!layers.includes(line) || $("sosPanel").style.transform) return;   // 지운 선이거나 팝업이 아직 움직이는 중이면 그냥 둠 (다 커지면 다시 그림)
      map.relayout(); line.setMap(null); line.setMap(map);
    };
    const onIdle = () => { K.event.removeListener(map, "idle", onIdle); again(); };
    K.event.addListener(map, "idle", onIdle);
    requestAnimationFrame(again); setTimeout(again, 400);
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
    row(chip(bed, ""), upd ? `<span class="chip upd">${upd[0]} <span class="nb">${upd[1]} 기준</span></span>` : ""),   // 줄이 넘치면 날짜 / 시각 기준 으로 나뉨
  ];
  // 병원이 올린 진료 제한 메시지 (예: '정형외과 전문의 부재로 수지접합 불가'): 블럭 아래 한 줄씩
  const msg = (er.msgs || []).map((m) => `<span class="chip msg">${esc(m)}</span>`).join("");
  // 항목 끝 괄호 부분(예: '(1500g 이상, 30주 이상)')은 한 덩어리: 줄이 넘치면 괄호 부분이 통째로 다음 줄로 (브라우저마다 '(' 뒤에서 끊는 것 방지)
  // tail(표시 아이콘)은 마지막 덩어리(괄호 부분 또는 마지막 낱말)에 붙여 혼자 다음 줄로 떨어지지 않게
  const item = (x, tail = "") => {
    const m = /^(.*?)\s*(\([^()]*\))$/.exec(x);
    if (m) return `${esc(m[1])} <span class="paren">${esc(m[2])}${tail}</span>`;
    const k = x.lastIndexOf(" ");
    return tail ? `${esc(x.slice(0, k + 1))}<span class="nw">${esc(x.slice(k + 1))}${tail}</span>` : esc(x);
  };
  // 중증질환 수용 정보 (칸 맨 아래): 누르면 같은 박스 안에서 펼쳐져 불가(위)·가능(아래) 항목을 모두 보여 줌 (다시 누르면 접힘)
  // 중증질환 수용 정보 (칸 맨 아래): 누르면 같은 박스 안에서 펼쳐짐 (다시 누르면 접힘). 27개 항목을 분야별로,
  //  분야 안에서는 제한 → 가능 → 정보 없음 줄 순서. 표시 아이콘은 칸 글자 뒤에
  // 표시 아이콘: ✕ 제한, ○ 가능, – 정보 없음. 같은 굵기의 선으로 그림 (글자 기호는 글꼴마다 굵기가 달라서)
  const MK = {
    가능: '<circle cx="8" cy="8" r="5.5"/>',
    불가능: '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/>',
    "정보 미제공": '<path d="M3 8h10"/>',
  };
  const LABEL = { 불가능: "제한", 가능: "가능", "정보 미제공": "정보 없음" };   // 화면에 쓰는 말 (서버 값은 그대로)
  const mk = (st) => `<svg class="mk" viewBox="0 0 16 16" aria-label="${LABEL[st]}">${MK[st]}</svg>`;
  const ST = ["불가능", "가능", "정보 미제공"];   // 분야 안 줄 순서
  const sv = Array.isArray(er.serious) ? er.serious : [];
  // 분야 순서: 제한이 있는 분야 → 가능이 있는 분야 → 정보 없음만 있는 분야 (같은 단계 안에서는 원래 순서). 기타는 늘 맨 아래
  const tier = (g) => g === "기타" ? 3 : sv.some((x) => x.group === g && x.status === "불가능") ? 0 : sv.some((x) => x.group === g && x.status === "가능") ? 1 : 2;
  const groups = [...new Set(sv.map((x) => x.group))].map((g, k) => [g, k]).sort((a, b) => tier(a[0]) - tier(b[0]) || a[1] - b[1]).map(([g]) => g);
  // 범례: 펼치기 버튼 바로 아래, 접혀 있어도 보임. 같은 너비 블럭 3개(배경색)에 개수까지 (예: ✕ 제한 2)
  const legend = `<div class="lg3">${ST.map((st) => `<span class="chip" data-fit="1">${mk(st)}${LABEL[st]} ${sv.filter((x) => x.status === st).length}</span>`).join("")}</div>`;
  const sevBody = groups.map((g) => `<p class="sub">${esc(g)}</p><div class="grp">` + ST.map((st) => {   // 분야 이름은 박스 위, 항목은 분야마다 박스로 감쌈
    const xs = sv.filter((x) => x.group === g && x.status === st);
    return xs.length ? `<div class="row">${xs.map((x) => `<span class="chip">${item(x.msg ? `${x.name} (${x.msg})` : x.name, mk(st))}</span>`).join("")}</div>` : "";
  }).join("") + "</div>").join("");
  const sev = sv.length
    ? `<div class="sevbox"><button type="button" class="chip tog" data-tog aria-expanded="false">진료과 별 진료 가능 정보</button>${legend}<div class="sev" hidden>${sevBody}</div></div>` : "";
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
  fitText();
  sortMsgs(wrap);
  wrap.querySelectorAll(".sevbox").forEach(fitLegend);
  if (first) roll(wrap);
}
// 칸 안 글자의 가장 긴 줄 가로 길이 (한 줄이면 그 줄, 여러 줄이면 그중 가장 긴 줄)
function longestLine(el) {
  const rows = new Map(), tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;
  let top0 = null;
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const x of rg.getClientRects()) {
      if (!x.width) continue;
      top0 ??= x.top;
      const k = Math.round((x.top - top0) / lh), o = rows.get(k);
      rows.set(k, o ? [Math.min(o[0], x.left), Math.max(o[1], x.right)] : [x.left, x.right]);
    }
  }
  return Math.max(0, ...[...rows.values()].map(([l, r]) => r - l));
}
// 진료 제한 메시지: 한 줄이든 여러 줄이든 화면에 보이는 가장 긴 줄의 가로 길이가 짧은 칸부터 위로 (병원이 올린 순서 대신)
function sortMsgs(wrap) {
  for (const box of wrap.querySelectorAll(".info")) {
    const ms = [...box.querySelectorAll(".chip.msg")];
    if (ms.length < 2) continue;
    const w = new Map(ms.map((m) => [m, longestLine(m)]));
    const after = ms[ms.length - 1].nextSibling, parent = ms[0].parentNode;
    for (const m of ms.sort((a, b) => w.get(a) - w.get(b))) parent.insertBefore(m, after);
  }
}
// 병원 이름을 뺀 칸 안 글씨 크기: '11월 11일 13:03 기준' 칸이 한 줄에 들어가는 크기로 모두 같이 (최대 14px)
function fitText() {
  const wrap = $("sosInfo");
  wrap.style.fontSize = "";
  const c = wrap.querySelector(".chip.upd");
  if (!c) return;
  const cs = getComputedStyle(c), avail = c.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  c.style.flexWrap = "nowrap"; c.style.whiteSpace = "nowrap";
  const rg = document.createRange(); rg.selectNodeContents(c);
  const need = rg.getBoundingClientRect().width;
  c.style.flexWrap = c.style.whiteSpace = "";
  const fs = parseFloat(getComputedStyle(wrap).fontSize);
  if (need > avail - 1) wrap.style.fontSize = `${Math.floor(fs * (avail - 1) / need * 10) / 10}px`;
}
// 응급실 정보 칸: 처음 나타날 때 카메라 팝업 단어 칸처럼 아래로 늘어나며 내려옴 (0.5초, 같은 곡선). 지도 칸과의 12px 간격도 같이 벌어짐
const ROLL = { duration: 500, easing: "cubic-bezier(.3,.7,.2,1)" };
function roll(el) {
  const h = el.getBoundingClientRect().height, m = getComputedStyle(el).marginTop;
  el.style.overflow = "hidden";
  el.animate([{ height: "0px", marginTop: "0px" }, { height: `${h}px`, marginTop: m }], ROLL).onfinish = () => { el.style.overflow = ""; };
}
// 두 줄 이상으로 넘친 블럭은 박스 너비를 가장 긴 줄에 맞추고, 그 줄에는 그 블럭만 둠 (그냥 두면 줄이 바뀌어도 박스는 한 줄 전체 너비로 남음)
// 중증질환 항목 줄 정리: 병원이 적은 순서 대신, 줄마다 길이가 최대한 같아 전체가 직사각형에 가깝게 보이도록 다시 배치
//  가장 적은 줄 수로 넣되, 긴 항목부터 지금 가장 짧은 줄에 넣음. 한 줄에 다 안 들어가는 두 줄 칸은 늘 맨 위 (좁은 것부터, 두 개가 한 줄에 들어가면 같이)
function arrange(root) {
  for (const row of root.querySelectorAll(".row")) {
    const chips = [...row.querySelectorAll(".chip")];
    chips.forEach((c) => { c.style.width = c.style.marginInline = ""; delete c.dataset.fit; delete c.dataset.lines; });
    row.replaceChildren(...chips);   // 예전 줄 나눔 풀기
    const cap = row.clientWidth, gap = parseFloat(getComputedStyle(row).columnGap) || 6;
    if (!cap || chips.length < 2) { const l = document.createElement("div"); l.className = "line"; l.append(...chips); row.replaceChildren(l); continue; }
    const w = new Map(chips.map((c) => { c.style.width = "max-content"; const x = c.getBoundingClientRect().width; c.style.width = ""; return [c, x]; }));
    const big = chips.filter((c) => w.get(c) > cap), small = chips.filter((c) => w.get(c) <= cap).sort((a, b) => w.get(b) - w.get(a));
    const total = small.reduce((t, c) => t + w.get(c) + gap, 0);
    // 1순위: 가장 적은 줄 수. 2순위: 그 줄 수 안에서 줄 길이가 최대한 고르게 (들쭉날쭉하지 않게)
    const W = (c) => w.get(c), len = (b) => b.reduce((t, c) => t + W(c), 0) + gap * Math.max(0, b.length - 1);
    const pack = (k, pick) => {   // 긴 것부터 k 줄에 넣기. pick: 넣을 줄 고르는 방법. 못 넣으면 null
      const bins = Array.from({ length: k }, () => []);
      for (const c of small) {
        const fits = bins.filter((b) => len(b) + (b.length ? gap : 0) + W(c) <= cap + 0.5);
        if (!fits.length) return null;
        pick(fits).push(c);
      }
      return bins;
    };
    const spread = (bins) => { const l = bins.map(len); return Math.max(...l) - Math.min(...l); };
    const even = (bins) => {   // 두 줄 사이에서 하나 옮기거나 맞바꿔 길이 차이를 줄일 수 있으면 계속
      for (let more = true, n = 0; more && n < 200; n++) {
        more = false;
        for (let i = 0; i < bins.length && !more; i++) for (let j = 0; j < bins.length && !more; j++) {
          if (i === j) continue;
          const A = bins[i], B = bins[j], before = spread(bins);
          for (let a = 0; a < A.length && !more; a++) {
            const mv = [A.filter((_, x) => x !== a), [...B, A[a]]];   // A 의 하나를 B 로
            if (mv[0].length && len(mv[1]) <= cap + 0.5) { const t = [...bins]; t[i] = mv[0]; t[j] = mv[1]; if (spread(t) < before - 0.5) { bins[i] = mv[0]; bins[j] = mv[1]; more = true; break; } }
            for (let b = 0; b < B.length && !more; b++) {           // 맞바꾸기
              const nA = [...A]; nA[a] = B[b]; const nB = [...B]; nB[b] = A[a];
              if (len(nA) <= cap + 0.5 && len(nB) <= cap + 0.5) { const t = [...bins]; t[i] = nA; t[j] = nB; if (spread(t) < before - 0.5) { bins[i] = nA; bins[j] = nB; more = true; } }
            }
          }
        }
      }
      return bins;
    };
    let lines = null;
    for (let k = Math.max(1, Math.ceil(total / (cap + gap))); !lines && k <= small.length; k++) {
      const tries = [pack(k, (f) => f.sort((a, b) => len(a) - len(b))[0]), pack(k, (f) => f[0])].filter(Boolean).map(even);
      if (tries.length) lines = tries.sort((a, b) => spread(a) - spread(b))[0].filter((b) => b.length).sort((a, b) => len(a) - len(b));   // 줄 길이(칸 가로 길이 + 사이 간격)가 짧은 줄부터 위로
    }
    lines ??= small.map((c) => [c]);
    // 여러 줄 칸: 혼자 한 줄에 두고 줄였을 때 너비·줄 수를 잰 뒤 맨 위에. 몇 줄이든 상관없이 좁은 것부터,
    //  줄 수가 같은 칸끼리만 한 줄에 같이 (줄 수가 다른 칸을 나란히 두면 높이가 달라 어색함). 그 아래 한 줄 칸들
    const line = (items) => { const l = document.createElement("div"); l.className = "line"; l.append(...items); return l; };
    const bw = new Map(big.map((c) => { row.append(line([c])); return [c, lineFit(c) || cap]; }));
    const bigLines = [];
    const nl = (c) => +c.dataset.lines || 2;
    for (const c of big.sort((a, b) => bw.get(a) - bw.get(b))) {   // 좁은 것부터, 같은 줄 수 칸끼리만 한 줄에 같이
      const same = bigLines.find((b) => b.n === nl(c) && b.sum + gap + bw.get(c) <= cap + 0.5);
      if (same) { same.sum += gap + bw.get(c); same.items.push(c); }
      else bigLines.push({ n: nl(c), sum: bw.get(c), items: [c] });
    }
    for (const b of bigLines) for (const c of b.items) {
      c.dataset.fit = "1"; c.style.width = `${bw.get(c)}px`;
      c.style.marginInline = b.items.length === 1 ? `calc((100% - ${bw.get(c)}px) / 2)` : "";   // 혼자면 양옆 여백으로 줄을 채움
    }
    bigLines.sort((a, b) => a.sum - b.sum);   // 여러 줄 칸 줄도 짧은 줄부터
    row.replaceChildren(...[...bigLines.map((b) => b.items), ...lines].map(line));
  }
}
// 두 줄 이상으로 넘친 칸의 너비를 가장 긴 줄에 맞춘 값 (한 줄이면 null). 그냥 두면 줄이 바뀌어도 박스는 한 줄 전체 너비로 남음
function lineFit(el) {
  // 글자 조각만 잼: 괄호 덩어리(inline-block)는 안에서 줄이 바뀌면 상자 자체가 칸 너비만큼 넓어서, 그 상자 대신 안의 글자로 잼
  const rects = [], tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) { const rg = document.createRange(); rg.selectNodeContents(n); rects.push(...[...rg.getClientRects()].filter((x) => x.width)); }
  rects.push(...[...el.querySelectorAll("svg.mk")].map((x) => x.getBoundingClientRect()));   // 표시 아이콘도 글자처럼
  // 조각을 줄 높이 기준으로 줄마다 묶음 (글씨 크기에 따라 같은 줄 조각의 위치가 1px 남짓 어긋날 수 있어서)
  const cs = getComputedStyle(el), lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
  if (!rects.length) return null;
  const top0 = Math.min(...rects.map((x) => x.top)), rows = new Map();
  for (const x of rects) { const k = Math.round((x.top - top0) / lh), o = rows.get(k); rows.set(k, o ? [Math.min(o[0], x.left), Math.max(o[1], x.right)] : [x.left, x.right]); }
  if (rows.size < 2) return null;
  const pad = cs.boxSizing === "border-box" ? parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) : 0;
  el.dataset.lines = rows.size;   // 몇 줄인지 (arrange 가 줄 수별로 묶을 때 씀)
  return Math.ceil(Math.max(...[...rows.values()].map(([l, r]) => r - l))) + pad + 1;
}
// 범례 블럭 3개(같은 너비)에 글자가 한 줄로 안 들어가면 범례 글씨만 들어가는 만큼 줄임
function fitLegend(root) {
  const lg = root.querySelector(".lg3");
  if (!lg) return;
  lg.style.fontSize = "";
  // 칸 안 양옆에 글자 하나 남짓(6px) 여유를 두고 맞춤 (딱 맞추면 기기·글꼴에 따라 넘치거나 꽉 차 보임)
  const r = Math.max(...[...lg.querySelectorAll(".chip")].map((c) => (c.scrollWidth + 6) / (c.clientWidth || 1)));
  if (r > 1) lg.style.fontSize = `${Math.floor(parseFloat(getComputedStyle(lg).fontSize) / r * 10) / 10}px`;
}
function fitChips(root) {
  for (const el of root.querySelectorAll(".chip")) {
    if (el.dataset.fit === "1") continue;   // arrange 가 이미 맞춘 두 줄 칸
    el.style.width = el.style.marginInline = "";
    const w = lineFit(el);
    if (!w) continue;
    el.style.width = `${w}px`;
    el.style.marginInline = `calc((100% - ${w}px) / 2)`;   // 양옆 여백으로 줄을 채워, 두 줄 블럭이 있는 줄에는 그 블럭만
  }
}
// 진료과 별 정보 펼치기·접기. 응급실 정보 칸이 처음 나올 때와 같은 효과: 펼칠 때 아래로 늘어나고, 접을 때 위로 줄어든 뒤 숨김
//  quiet: 다른 병원을 고르면서 접을 때는 화면을 따로 움직이지 않음 (병원 칸 이동·스크롤과 겹치지 않게)
function toggleSev(t, quiet = false) {
  const box = t.parentElement.querySelector(".sev"), open1 = box.hidden || box.dataset.closing === "1";
  t.setAttribute("aria-expanded", String(open1));
  box.getAnimations().forEach((a) => a.cancel());
  box.hidden = false; box.dataset.closing = "";
  arrange(box); fitChips(box);
  const cs = getComputedStyle(box), h = box.getBoundingClientRect().height;
  if (open1 && !quiet) scrollInto(t.getBoundingClientRect().top - 16);   // 펼치면 제목(진료과 별 진료 가능 정보)이 화면 맨 위(16px)에 오도록
  const full = { height: `${h}px`, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom }, zero = { height: "0px", paddingTop: "0px", paddingBottom: "0px" };
  box.style.overflow = "hidden";
  if (!open1) { box.dataset.closing = "1"; if (!quiet) shrinkPage(h); }
  box.animate(open1 ? [zero, full] : [full, zero], ROLL).onfinish = () => {
    box.style.overflow = "";
    if (!open1) { box.hidden = true; box.dataset.closing = ""; document.documentElement.style.minHeight = baseMin; }
  };
}
// 병원 칸 자리 바꾸기: 카메라 팝업 낱말 블럭처럼 예전 자리에서 새 자리로 미끄러짐 (FLIP, 0.45초 같은 곡선)
const MOVE = { duration: 450, easing: "cubic-bezier(.3,.7,.2,1)" };
function moveFirst(b) {
  const wrap = $("sosInfo"), boxes = [...wrap.querySelectorAll(".info[data-i]")];
  if (boxes[0] === b) return;
  const before = new Map(boxes.map((x) => [x, x.getBoundingClientRect().top]));
  wrap.prepend(b);
  for (const x of boxes) {
    const dy = before.get(x) - x.getBoundingClientRect().top;
    if (Math.abs(dy) >= 1) x.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], MOVE);
  }
}
// 응급실 칸을 누르면 위 지도의 경로가 그 병원으로 (1순위 ↔ 2순위)
$("sosInfo").addEventListener("click", (e) => {
  const t = e.target.closest?.("[data-tog]");
  if (t) { toggleSev(t); return; }               // 진료과 별 정보 펼치기·접기 (지도 경로는 바꾸지 않음)
  const b = e.target.closest?.(".info[data-i]");
  if (!b || !shown || !lastPos) return;
  const i = +b.dataset.i, list = shown.ers || [];
  if (!list[i] || i === sel) return;
  sel = i;
  for (const x of $("sosInfo").querySelectorAll(".info[data-i]")) {
    x.classList.toggle("sel", +x.dataset.i === sel);   // 도착 병원 칸 빨강
    const open = x !== b && x.querySelector("[data-tog][aria-expanded='true']");
    if (open) toggleSev(open, true);                    // 다른 병원 칸에 펼쳐 둔 진료과 별 정보는 접음
  }
  moveFirst(b);                                          // 고른 병원 칸을 지도 바로 아래(맨 위)로
  drawMap(lastPos, list[i]);
  // 바뀐 경로를 바로 볼 수 있게 언제나 팝업을 열 때 자리(입력창이 화면 위쪽 16px)로
  scrollInto($("form").getBoundingClientRect().top - 16);
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
// 팝업이 완전히 닫힐 때까지 기다림 (카메라 팝업이 SOS 를 닫고 시작할 때)
let closeWait = [];
const whenClosed = () => ($("sosPanel").hidden ? Promise.resolve() : new Promise((r) => closeWait.push(r)));
window.__sos = { whenClosed, isOpen: () => open };
// 카메라 팝업이 뜨면, 아직 열리기 전에 기다리던 SOS 요청(위치 묻기 등)은 취소
addEventListener("sign-open", () => { if (!open) { gen++; cancelLocate(); } });
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
    if (!pop.to) { $("sosPanel").hidden = true; document.body.classList.remove("sos-open"); document.documentElement.style.minHeight = baseMin = ""; closeWait.splice(0).forEach((f) => f()); }
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
  dispatchEvent(new Event("sos-open"));   // 카메라 쪽: 아직 열리기 전에 기다리던 카메라 요청은 취소
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
// 지금 위치 찾기 (SOS 를 누를 때마다 새로). 누른 그 순간 바로 시작해야 Safari 등이 위치 요청을 사용자 동작으로 받아 줌
//  - 빠른 위치(와이파이·기지국)를 계속 기다리다가 3초 뒤부터는 정밀 위치도 함께. 먼저 온 것을 씀
//  - '잠깐 못 찾음' 오류는 무시하고 계속 기다림 (Mac·Safari 는 첫 위치 전에 이런 오류를 자주 냄). 거절이면 바로 멈춤
//  - 30초 안에 못 찾으면 이 기기에서 마지막으로 찾았던 위치를 씀
//  - 다시 누르면 앞의 요청은 모두 지우고 새로 (권한 창을 Esc 등으로 그냥 닫아 앞의 요청이 끝나지 않고 남아 있으면 새 권한 창이 안 뜸)
//  - 30초가 지나도 위치가 없을 때 마지막 위치를 쓰는 건 권한이 허용된 경우만 (권한 창을 닫았는데 나중에 팝업이 뜨지 않게)
let cancelLocate = () => {};
function locate() {
  cancelLocate();
  return new Promise((res, rej) => {
    const g = navigator.geolocation;
    if (!g) return pos ? res(pos) : rej(new Error("no geolocation"));
    const ids = [];
    let done = false, t2 = 0, t30 = 0;
    const end = (f, v) => { if (done) return; done = true; ids.forEach((i) => g.clearWatch(i)); clearTimeout(t2); clearTimeout(t30); f(v); };
    cancelLocate = () => end(rej, new Error("cancelled"));
    const ok = (p) => { pos = { lat: p.coords.latitude, lng: p.coords.longitude }; posAt = Date.now(); end(res, pos); };
    const bad = (e) => { if (e && e.code === 1) end(rej, e); };   // 거절만 멈춤, 나머지 오류는 계속 기다림
    ids.push(g.watchPosition(ok, bad, { enableHighAccuracy: false, maximumAge: 60000 }));
    t2 = setTimeout(() => { if (!done) ids.push(g.watchPosition(ok, bad, { enableHighAccuracy: true, maximumAge: 0 })); }, 3000);
    t30 = setTimeout(async () => {
      const st = await navigator.permissions?.query({ name: "geolocation" }).catch(() => null);
      if (pos && st?.state === "granted") end(res, pos); else end(rej, new Error("timeout"));
    }, 30000);
  });
}
$("sosBtn").onclick = async () => {
  if (open) return closePanel();
  if (!$("sosPanel").hidden) return openPanel(null);   // 닫히는 도중: 보던 그대로 다시 (찾던 위치·조회도 이어서)
  const my = ++gen;
  // 카메라 팝업이 떠 있거나 닫히는 중이면 완전히 닫힌 뒤에 시작 (권한 묻기도 그다음)
  if (window.__sign?.whenClosed) { await window.__sign.whenClosed(); if (my !== gen) return; }
  const where = locate();                              // 위치 찾기 시작 (권한 창도 이때 뜸)
  // 이미 위치를 허락해 둔 기기면 위치가 나오기를 기다리지 않고 바로 팝업. 처음이면 '허용'을 누르는 순간 팝업. 거절하면 열지 않음
  const st = await navigator.permissions?.query({ name: "geolocation" }).catch(() => null);
  if (my !== gen) return;
  if (st && st.state === "granted") openPanel(null);
  else if (st) st.onchange = () => { if (st.state === "granted" && !open && my === gen && shownGen !== my) openPanel(null); st.onchange = null; };
  try {
    const p = await where;
    if (my !== gen) return;
    if (shownGen !== my) openPanel(p); else refresh(p);
  } catch { if (my === gen && shownGen === my) showInfo(null, true); }
};
// SOS 팝업은 SOS 버튼을 다시 눌러야만 닫힘 (팝업 밖 배경·버튼을 눌러도 그대로)
addEventListener("resize", () => { if (open) { window.__sign?.place?.($("sosPanel")); map?.relayout(); fitText(); $("sosInfo").querySelectorAll(".sev:not([hidden])").forEach((b) => { arrange(b); fitChips(b); }); $("sosInfo").querySelectorAll(".sevbox").forEach(fitLegend); } });
