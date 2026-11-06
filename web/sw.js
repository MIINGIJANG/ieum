// 오프라인 모드 (서비스 워커): 인터넷이 될 때는 늘 서버에서 새로 받고(지금과 똑같이 동작),
// 받은 것을 저장해 둠 → 인터넷이 끊기면 저장해 둔 것으로 보여줌.
//   - 화면·아바타·라이브러리·출처 쪽 이미지 (GET)
//   - 질문 답 (POST /query): 처음 화면에서 예시 질문 답을 미리 받아 두므로 예시 질문은 오프라인에서도 됨
//   - 단어 동작 (POST /motions)
//   - 카메라 수어 입력 모델 (/sign/models·lib): 이것만 저장해 둔 것을 먼저 씀 (크고 거의 안 바뀜)
// 화면 모양·문구는 바꾸지 않음. 팀원 SignBridge 의 오프라인 준비(useOfflineReady)를 참고.
const CACHE = "ieum-offline-v1";
const SHELL = ["./", "./index.html", "./avatar.js", "./avatar-common.js", "./avatar-vrm.js", "./rig.js", "./face.js",
  "./handshapes.json", "./models/avatar.glb", "./vendor/three/three.module.js", "./vendor/three-vrm.module.min.js"];

self.addEventListener("install", (e) => {
  // 하나가 실패해도 나머지는 저장 (없는 파일이 있어도 설치는 됨)
  e.waitUntil(caches.open(CACHE).then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => null)))));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// POST 는 Cache API 에 바로 못 넣어서, 요청 내용으로 만든 GET 주소를 열쇠로 씀
async function postKey(req) {
  const url = new URL(req.url);
  let body = {};
  try { body = await req.clone().json(); } catch { return null; }
  if (url.pathname.endsWith("/query")) {
    if (body.seen) return null;                      // 화면에 보였다는 기록용 (답이 없음) → 저장 안 함
    return `${url.origin}/__offline__/query?text=${encodeURIComponent((body.text || "").trim())}&beam=${body.beam || 4}`;
  }
  if (url.pathname.endsWith("/motions")) {
    return `${url.origin}/__offline__/motions?g=${encodeURIComponent((body.glosses || []).join("|"))}`;
  }
  return null;
}

// 카메라 수어 입력의 큰 파일(인식 모델 25MB·관절 점 모델·MediaPipe/ONNX 라이브러리, 약 60MB)은 한 번 받으면 저장해 둔 것을 바로 씀
// (새로고침해도 다시 받지 않음). 파일을 바꿀 때는 위 CACHE 이름을 바꾸면 새로 받음
const BIG = /\/sign\/(models|lib)\//;
async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone()).catch(() => null);
  return res;
}

async function networkFirst(req, key) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(key || req, res.clone()).catch(() => null);
    return res;
  } catch (err) {
    const hit = await cache.match(key || req, { ignoreSearch: false });
    if (hit) return hit;
    if (req.mode === "navigate") {                    // 처음 화면은 주소가 달라도 저장해 둔 화면으로
      const shell = await cache.match("./index.html") || await cache.match("./");
      if (shell) return shell;
    }
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;     // 글꼴 등 다른 사이트는 브라우저에 맡김
  if (req.method === "GET") {
    e.respondWith(BIG.test(url.pathname) ? cacheFirst(req) : networkFirst(req));
    return;
  }
  if (req.method === "POST" && (url.pathname.endsWith("/query") || url.pathname.endsWith("/motions"))) {
    e.respondWith((async () => {
      const key = await postKey(req);
      return networkFirst(req, key);
    })());
  }
});
