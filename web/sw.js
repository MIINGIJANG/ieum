// 오프라인 모드 (서비스 워커). 화면 모양·문구는 바꾸지 않음.
//
// 1) 웹 파일(화면·아바타·라이브러리·출처 쪽 이미지·카메라 수어 인식 모델): 서버의 파일 목록(/offline/manifest, 파일마다 내용 해시)과
//    저장해 둔 것을 비교 → 같으면 내려받지 않고 저장해 둔 것을 바로 씀, 다르면 그 파일만 새로 받음.
//    · 인터넷이 될 때: 요청 한 번(목록)으로 최신인지 확인하므로 늘 최신 화면 + 다시 내려받지 않아 빠름
//    · 인터넷이 안 될 때: 저장해 둔 것으로 그대로 동작
// 2) 첫 로딩이 끝나면 목록의 파일을 뒤에서 모두 받아 둠(미리 저장) → 그 뒤로는 인터넷 없이도 화면·아바타·카메라 수어 입력이 됨
// 3) 질문 답(POST /query)·단어 동작(POST /motions): 서버에서 받을 때마다 저장, 인터넷이 끊기면 저장해 둔 답으로
//    (첫 화면이 예시 질문의 답과 그 단어 동작을 미리 받아 두므로 예시 질문은 오프라인에서도 됨)
// 4) 글꼴(Google Fonts): 한 번 받은 글꼴은 저장해 둔 것을 씀
// SOS(/sos/, 위치·응급실)는 실시간 정보라 저장하지 않음.
const CACHE = "ieum-offline-v2";
const META_URL = "/__offline__/meta";              // 저장해 둔 파일의 해시와 마지막으로 받은 목록
const MANIFEST_URL = "./offline/manifest";
const MANIFEST_FRESH_MS = 15000;                   // 한 번 받은 목록은 15초 동안 그대로 씀 (화면 하나가 뜰 때 요청 수십 개가 한꺼번에 옴)
const MANIFEST_TIMEOUT_MS = 3000;                  // 목록이 3초 안에 안 오면 인터넷이 안 되는 것으로 보고 저장해 둔 것으로
const FONT = /^https:\/\/fonts\.(googleapis|gstatic)\.com\//;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// ---------------- 저장해 둔 파일의 해시 · 목록 ----------------
let meta = null, metaSave = 0;
async function loadMeta() {
  if (meta) return meta;
  const hit = await (await caches.open(CACHE)).match(META_URL);
  meta = (hit && await hit.json().catch(() => null)) || { hashes: {}, manifest: null };
  return meta;
}
function saveMeta() {                               // 여러 파일이 한꺼번에 저장될 때 한 번만 씀
  clearTimeout(metaSave);
  metaSave = setTimeout(async () => {
    const c = await caches.open(CACHE);
    c.put(META_URL, new Response(JSON.stringify(meta), { headers: { "Content-Type": "application/json" } })).catch(() => null);
  }, 300);
}
let manifestAt = 0, manifestP = null, online = true;
/** 서버의 파일 목록. 못 받으면(오프라인) 마지막으로 받은 목록 */
function getManifest() {
  if (manifestP && Date.now() - manifestAt < MANIFEST_FRESH_MS) return manifestP;
  manifestAt = Date.now();
  manifestP = (async () => {
    const m = await loadMeta();
    try {
      const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), MANIFEST_TIMEOUT_MS);
      const res = await fetch(MANIFEST_URL, { cache: "no-store", signal: ctl.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error(res.status);
      m.manifest = await res.json();
      online = true;
      saveMeta();
    } catch { online = false; }
    return m.manifest;
  })();
  return manifestP;
}

// ---------------- 웹 파일 ----------------
const pathOf = (url) => (url.pathname.endsWith("/") ? url.pathname + (url.pathname === "/" ? "" : "index.html") : url.pathname);
async function webFile(req, url) {
  const cache = await caches.open(CACHE), m = await getManifest(), mt = await loadMeta();
  const path = pathOf(url), want = m && m.files[path];
  const key = want !== undefined ? url.origin + path : req;      // 목록에 있는 파일은 주소 뒤 ?… 를 빼고 저장
  const hit = await cache.match(key);
  // 저장해 둔 것이 최신이면(해시가 같음) 그대로. 인터넷이 안 되면 저장해 둔 것이 있기만 하면 그대로
  if (hit && ((want && mt.hashes[path] === want) || !online)) return hit;
  try {
    const res = await fetch(req);
    if (res.ok && res.status === 200) {
      cache.put(key, res.clone()).then(() => { if (want) { mt.hashes[path] = want; saveMeta(); } }).catch(() => null);
    }
    return res;
  } catch (err) {
    if (hit) return hit;
    if (req.mode === "navigate") {                    // 처음 화면은 주소가 달라도 저장해 둔 화면으로
      const shell = await cache.match(url.origin + "/");
      if (shell) return shell;
    }
    throw err;
  }
}

// 첫 로딩이 끝난 뒤(화면이 알려 줌) 목록의 파일을 모두 받아 둠. 이미 최신인 것은 건너뜀. 한 번에 2개씩
let precaching = null;
function precache() {
  return (precaching ??= (async () => {
    const m = await getManifest(), mt = await loadMeta();
    if (!m || !online) return;
    const cache = await caches.open(CACHE);
    const todo = m.precache.filter((p) => mt.hashes[p] !== m.files[p]);
    const one = async (p) => {
      try {
        const res = await fetch(p, { cache: "no-cache" });        // 브라우저가 방금 받은 것이 있으면 서버는 '안 바뀜'만 답함
        if (res.ok && res.status === 200) { await cache.put(self.location.origin + p, res); mt.hashes[p] = m.files[p]; saveMeta(); }
      } catch { /* 다음 방문 때 다시 */ }
    };
    const workers = [0, 1].map(async () => { while (todo.length) await one(todo.shift()); });
    await Promise.all(workers);
    // 목록에서 없어진 파일은 지움 (답·동작·글꼴은 그대로)
    for (const k of await cache.keys()) {
      const u = new URL(k.url);
      if (u.origin === self.location.origin && !u.pathname.startsWith("/__offline__/") && k.method === "GET"
          && mt.hashes[u.pathname] !== undefined && m.files[u.pathname] === undefined) { cache.delete(k); delete mt.hashes[u.pathname]; }
    }
    saveMeta();
  })().finally(() => { precaching = null; }));
}
self.addEventListener("message", (e) => { if (e.data && e.data.type === "precache") e.waitUntil(precache()); });

// ---------------- 답 · 동작 (POST) ----------------
// POST 는 Cache API 에 바로 못 넣어서, 요청 내용으로 만든 GET 주소를 열쇠로 씀.
// 질문은 띄어쓰기·문장 부호를 빼고 비교 ("정화통은 언제 교체해요?" = "정화통은 언제 교체해요")
const norm = (t) => String(t || "").replace(/[\s?？!！.,。~·]/g, "");
async function postKey(req) {
  const url = new URL(req.url);
  let body = {};
  try { body = await req.clone().json(); } catch { return null; }
  if (url.pathname.endsWith("/query")) {
    if (body.seen) return null;                      // 화면에 보였다는 기록용 (답이 없음) → 저장 안 함
    return `${url.origin}/__offline__/query?text=${encodeURIComponent(norm(body.text))}&beam=${body.beam || 4}`;
  }
  return null;
}
// 단어 동작(POST /motions {glosses}): 단어마다 따로 저장 → 오프라인에서는 요청한 단어 중 저장해 둔 것만 모아 답함
//  (요청마다 단어 묶음이 달라도 됨)
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };
const motionKey = (g) => `${self.location.origin}/__offline__/motion?g=${encodeURIComponent(g)}`;
async function motions(req) {
  const cache = await caches.open(CACHE);
  let body = {};
  try { body = await req.clone().json(); } catch { /* 빈 요청 */ }
  try {
    const res = await fetch(req);
    if (res.ok) res.clone().json().then((all) => {
      for (const [g, m] of Object.entries(all)) if (m && m.frames && m.frames.length) cache.put(motionKey(g), new Response(JSON.stringify(m), { headers: JSON_HEADERS })).catch(() => null);
    }).catch(() => null);
    return res;
  } catch (err) {
    const out = {};
    for (const g of body.glosses || []) { const hit = await cache.match(motionKey(g)); if (hit) out[g] = await hit.json(); }
    if (Object.keys(out).length) return new Response(JSON.stringify(out), { headers: JSON_HEADERS });
    throw err;
  }
}
async function networkFirst(req, key) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok && key !== null) cache.put(key || req, res.clone()).catch(() => null);
    return res;
  } catch (err) {
    const hit = key !== null && await cache.match(key || req);
    if (hit) return hit;
    throw err;
  }
}

// ---------------- 글꼴 ----------------
async function font(req) {
  const cache = await caches.open(CACHE), hit = await cache.match(req);
  const fresh = fetch(req).then((res) => { if (res.ok || res.type === "opaque") cache.put(req, res.clone()).catch(() => null); return res; });
  if (!hit) return fresh;
  if (req.url.includes("fonts.googleapis.com")) fresh.catch(() => null);   // 글꼴 목록(CSS)은 저장해 둔 것을 쓰고 뒤에서 새로 받아 둠
  return hit;                                                               // 글꼴 파일은 주소가 바뀌지 않으므로 저장해 둔 것 그대로
}

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (FONT.test(req.url) && req.method === "GET") { e.respondWith(font(req)); return; }
  if (url.origin !== self.location.origin) return;     // 지도 등 다른 사이트는 브라우저에 맡김
  if (url.pathname.startsWith("/sos/") || url.pathname.startsWith("/offline/")) return;   // SOS·파일 목록은 늘 서버에서
  if (req.method === "GET") {
    if (req.headers.has("range")) return;              // 부분 요청은 그대로
    e.respondWith((async () => {
      const m = await getManifest();
      // 웹 파일(목록에 있음)·화면 이동은 해시 비교, 그 밖의 GET(예시 질문 목록 등)은 서버 먼저·실패하면 저장해 둔 것
      return (m && m.files[pathOf(url)] !== undefined) || req.mode === "navigate" ? webFile(req, url) : networkFirst(req);
    })());
    return;
  }
  if (req.method === "POST" && url.pathname.endsWith("/query")) e.respondWith(postKey(req).then((key) => networkFirst(req, key)));
  else if (req.method === "POST" && url.pathname.endsWith("/motions")) e.respondWith(motions(req));
});
