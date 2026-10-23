// 3D 수어 아바타 (Three.js)
// 기본: 반실사 GLB 캐릭터 (Character Creator 4 / MPFB 등, web/models/avatar.glb). 비교용: 예전 VRM (?avatar=vrm)
//
// 동작 프레임(목=원점, 어깨너비=1, x 오른쪽 · y 아래 · z 카메라에서 멀어지는 쪽)을 '정규화 뼈대' 회전으로 바꿔 재생.
// 정규화 뼈대는 기본 자세에서 모든 뼈 회전이 0 → 뼈의 '기본 방향'은 기본 자세의 뼈 위치로 구함.
//   몸통·쇄골: 데이터 어깨선의 기울기·회전, 손 높이·뻗음으로 (상체가 같이 움직여야 뻣뻣하지 않음)
//   팔: 손목 위치 → 2관절 IK (팔꿈치 방향은 데이터 + 기본 방향을 섞고 시간에 따라 부드럽게)
//   손목: 손바닥 두 축으로 회전, 그중 비틀림 일부는 아래팔(또는 CC4 비틀림 뼈)에 나눠 손목이 꼬이지 않게
//   손가락: 관절 범위 제한(엄지 포함) + 마디 연동 + 자주 나오는 손 모양으로 살짝 끌어당김
//   단어 사이: 서버가 표시한 연결 구간(tr)은 앞·뒤 자세의 '회전'을 최소 저크 곡선으로 섞음
//   고개: 스프링(살짝 넘쳤다 돌아옴)으로, 목 40% · 머리 60%
//   얼굴: ARKit 표정 + 문법 표정 + 한국어 입 모양 + 시선
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { FINGERS, SIDES, V } from "./avatar-common.js";
import { VRMLook } from "./avatar-vrm.js";
import { buildRig } from "./rig.js";
import { FaceRig, mouthSyllables, mouthAt } from "./face.js";

const D = THREE.MathUtils.degToRad;
const clamp = THREE.MathUtils.clamp;
const minJerk = (t) => t * t * t * (10 - 15 * t + 6 * t * t);       // 가속도 변화가 가장 작은 곡선 (사람 팔 움직임에 가까움)

// 비수지 표현(NMS): 단어마다 붙는 표정·고개 움직임. 수어에서 표정은 문법·의미의 일부.
//   face : surprised(눈썹 올림·눈 크게) · angry(찡그림·긴장) · sad(걱정) · relaxed(부드러운 얼굴) 0~1
//   head : nod(끄덕임) · shake(도리도리) · tilt(갸웃) · up(위를 봄) · back(움찔), n = 반복 횟수, deg = 각도
export const NMS = {
  "위험1":    { face: { angry: 0.75, sad: 0.2 }, head: { type: "nod", n: 1, deg: 5 } },
  "조심1":    { face: { angry: 0.4, surprised: 0.2 }, head: { type: "nod", n: 1, deg: 7 } },
  "멈추다1":  { face: { angry: 0.65 }, head: { type: "nod", n: 1, deg: 10 } },
  "낙하물0#": { face: { surprised: 0.6 }, head: { type: "up", deg: 10 } },
  "감전0":    { face: { surprised: 0.5, angry: 0.25 }, head: { type: "back", deg: 4 } },
  "없다1":    { face: { sad: 0.3 }, head: { type: "shake", n: 2, deg: 14 } },
  "확인1":    { face: { surprised: 0.25 }, head: { type: "nod", n: 2, deg: 8 } },
  "이상하다1": { face: { sad: 0.35, angry: 0.15 }, head: { type: "tilt", deg: 6 } },
};
// 표정 이름 → ARKit 표정 조합 (반실사 얼굴은 눈썹·눈·입을 따로 움직일 수 있어 또렷하게)
const FACE_ARKIT = {
  surprised: { browInnerUp: 1, browOuterUpLeft: 0.9, browOuterUpRight: 0.9, eyeWideLeft: 0.5, eyeWideRight: 0.5 },
  angry: { browDownLeft: 1, browDownRight: 1, eyeSquintLeft: 0.5, eyeSquintRight: 0.5, noseSneerLeft: 0.25, noseSneerRight: 0.25, mouthPressLeft: 0.35, mouthPressRight: 0.35 },
  sad: { browInnerUp: 0.8, mouthFrownLeft: 0.6, mouthFrownRight: 0.6, mouthShrugLower: 0.2 },
  relaxed: { mouthSmileLeft: 0.45, mouthSmileRight: 0.45, cheekSquintLeft: 0.2, cheekSquintRight: 0.2 },
};
const GRAMMAR_GAIN = 1.25;   // 문법 표정은 과장해야 읽힘

// 단어 뜻 표정(NMS 표) + 문장 문법 표정(서버 ksl/order.py). 표정은 큰 값끼리, 고개 움직임은 문법 쪽이 우선
function mergeNMS(word, gram) {
  if (!word) return gram ? { face: scale(gram.face, GRAMMAR_GAIN), head: gram.head } : null;
  if (!gram) return word;
  const face = { ...word.face };
  for (const [k, v] of Object.entries(gram.face || {})) face[k] = Math.max(face[k] || 0, v * GRAMMAR_GAIN);
  return { face, head: gram.head || word.head };
}
const scale = (o, s) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, Math.min(1, v * s)]));

// 뼈마다 따라가는 빠르기(초): 손가락은 빠르게, 몸통은 천천히. 프레임 간 시간으로 계산 → 화면 주사율과 상관없이 같은 속도
function tauFor(name) {
  if (/Thumb|Index|Middle|Ring|Little/.test(name)) return 0.03;
  if (/Hand$/.test(name)) return 0.035;
  if (/Arm$/.test(name)) return 0.04;
  if (/Shoulder$/.test(name)) return 0.08;
  if (/spine|chest|Chest/.test(name)) return 0.12;
  return 0.05;
}

// 2차 스프링: 목표를 따라가되 살짝 넘쳤다 돌아옴 (zeta < 1). 고개·시선에 사용
class Spring {
  constructor(omega, zeta, n = 3) { this.w = omega; this.z = zeta; this.x = new Float64Array(n); this.v = new Float64Array(n); }
  step(target, dt) {
    const steps = Math.max(1, Math.ceil(dt / 0.008)), h = dt / steps;
    for (let s = 0; s < steps; s++) {
      for (let i = 0; i < this.x.length; i++) {
        const a = this.w * this.w * (target[i] - this.x[i]) - 2 * this.z * this.w * this.v[i];
        this.v[i] += a * h; this.x[i] += this.v[i] * h;
      }
    }
    return this.x;
  }
}

// 단어 고개 움직임 곡선: 단어 진행도 t(0~1) → [yaw, pitch(+아래), roll] 도
function headMove(H, t) {
  if (!H) return [0, 0, 0];
  const n = H.n || 1, env = Math.sin(Math.PI * clamp(t, 0, 1));
  const ph = clamp(t / 0.85, 0, 1) * n;                      // 단어 끝 15% 는 제자리로
  const bump = Math.sin(Math.PI * (ph % 1)) ** 2;            // 한 번 끄덕 = 내려갔다 올라옴 (부드러운 종 모양)
  switch (H.type) {
    case "nod": return [0, H.deg * bump * (ph < n ? 1 : 0), 0];
    case "shake": return [H.deg * Math.sin(2 * Math.PI * ph) * env, 0, 0];
    case "tilt": return [0, 0, H.deg * env];
    case "up": return [0, -H.deg * env, 0];
    case "back": return [0, -H.deg * Math.sin(Math.PI * clamp(t * 2, 0, 1)), 0];
    default: return [0, 0, 0];
  }
}

export class SignAvatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;   // 피부·옷 밝은 부분이 하얗게 날아가지 않게
    this.renderer.toneMappingExposure = 1.0;
    this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;   // 은은한 실내 반사광 (반실사 재질용)
    this.scene.environmentIntensity = 0.55;
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.1, 20);
    // 3점 조명: 앞 오른쪽 위 주광(따뜻) + 왼쪽 보조광(차갑게, 약하게) + 뒤 윤곽광 → 얼굴 굴곡·손 윤곽이 또렷하게
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 0.35));
    const key = new THREE.DirectionalLight(0xfff1e0, 2.1); key.position.set(0.9, 1.9, 2.2);
    const fill = new THREE.DirectionalLight(0xdfe8ff, 0.7); fill.position.set(-1.6, 1.2, 1.6);
    const rim = new THREE.DirectionalLight(0xffffff, 1.4); rim.position.set(-0.4, 2.2, -2.0);
    this.scene.add(key, fill, rim);
    this.vrm = null;
    this.driven = new Set();
    this.margin = 0.035;          // 손목이 몸 표면보다 최소 이만큼(m) 앞에 오도록
    this.st = { pole: {}, hand: {}, twist: { left: 0, right: 0 } };   // 시간에 따라 이어지는 상태
    this.headSpring = new Spring(13, 0.72);
    this.gazeSpring = new Spring(18, 0.85, 2);
    this.last = performance.now();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas);
  }

  async load(url) {
    const loader = new GLTFLoader();
    const isVRM = /\.vrm$/i.test(url);
    if (isVRM) loader.register((parser) => new VRMLoaderPlugin(parser));
    const t0 = performance.now();
    const gltf = await loader.loadAsync(url);
    this._loadMs = Math.round(performance.now() - t0);
    this.timings = {};
    const step = (name, fn) => { const t = performance.now(); fn(); this.timings[name] = Math.round(performance.now() - t); };
    if (isVRM) {
      const vrm = gltf.userData.vrm;
      VRMUtils.removeUnnecessaryVertices(gltf.scene);
      VRMUtils.combineSkeletons(gltf.scene);
      VRMUtils.rotateVRM0(vrm);
      vrm.isVRM = true;
      this.vrm = vrm;
      this.root = vrm.scene;
      this.root.traverse((o) => { o.frustumCulled = false; });
      this.scene.add(this.root);
      step("slimTorso", () => this._slimTorso());
      step("captureRest", () => this._captureRest());
      step("dressUp", () => this._dressUp());
      step("tightTop", () => this._tightTop());
      step("hideCoveredBody", () => this._hideCoveredBody());
      step("shortenSleeves", () => this._shortenSleeves());
      step("eyeColor", () => this._eyeColor());
      step("bareHands", () => this._bareHands());
      step("workwear", () => this._workwear());
      step("buzzCut", () => this._buzzCut());
      step("addHandColliders", () => this._addHandColliders());
      step("styleMaterials", () => this._styleMaterials());
      this.face = new FaceRig(null, vrm);
      this._gazeObj = new THREE.Object3D();
      this.scene.add(this._gazeObj);
      if (vrm.lookAt) vrm.lookAt.target = this._gazeObj;
    } else {
      let config = {};
      try { const r = await fetch(url.replace(/\.glb$/i, ".json")); if (r.ok) config = await r.json(); } catch { /* 설정 없음 */ }
      this.config = config;
      const rig = buildRig(gltf, config);
      this.vrm = rig;
      this.root = rig.scene;
      this.root.traverse((o) => { o.frustumCulled = false; });
      this.scene.add(this.root);
      step("styleGLB", () => this._styleGLB(config));
      step("captureRest", () => this._captureRest());
      step("hardHat", () => { if (config.hat !== false) this._hardHat(); });
      this.face = new FaceRig(this.root);
    }
    try { const r = await fetch(new URL("handshapes.json", new URL(url, location.href).href.replace(/models\/[^/]*$/, ""))); if (r.ok) this.handshapes = await r.json(); } catch { /* 없으면 손 모양 끌어당김 안 함 */ }
    step("measureBodyFront", () => this._measureBodyFront(this._hipsY, this.neckY));
    this._frameCamera();
    this.setFrame(null);
    this._loop();
    return this;
  }

  bone(name) { return this.vrm.humanoid.getNormalizedBoneNode(name); }
  raw(name) { return this.vrm.humanoid.getRawBoneNode(name); }

  // 반실사 GLB 재질: 피부는 덜 번들거리게, 옷은 짙게(손이 옷 앞에서 잘 보이게), 옷이 몸에 묻혀 피부가 비치지 않게
  _styleGLB(config) {
    const clothRe = new RegExp(config.clothes || "suit|cloth|shirt|jacket|pant|top|vest|uniform|coat", "i");
    const tint = new THREE.Color(config.clothTint || 0x4a5568);
    this.root.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of [].concat(o.material)) {
        const name = (m.name || "") + " " + o.name;
        if (/body|skin|head|arm|hand|leg/i.test(name) && !clothRe.test(name)) {
          if ("roughness" in m) { m.roughness = Math.max(m.roughness ?? 0.6, 0.58); m.metalness = 0; }
        }
        if (clothRe.test(name)) {
          if (config.clothTint !== false && m.color) m.color.multiply(tint);
          m.polygonOffset = true; m.polygonOffsetFactor = -1; m.polygonOffsetUnits = -2;   // 옷이 몸보다 살짝 앞에 그려짐
          if ("roughness" in m) m.roughness = Math.max(m.roughness ?? 0.8, 0.8);
        }
        if (/hair|lash|brow/i.test(name) && m.map) { m.alphaTest = Math.max(m.alphaTest || 0, 0.4); m.transparent = false; }
        if (config.roughness != null && "roughness" in m) { m.roughnessMap = null; m.roughness = config.roughness; m.needsUpdate = true; }   // 번들거림 없애기 (모델 설정)
      }
    });
  }

  // 기본 자세에서 각 뼈의 기본 방향(모델 좌표) 기록
  _captureRest() {
    const h = this.vrm.humanoid;
    h.resetNormalizedPose();
    this.vrm.update(0);
    this.root.updateMatrixWorld(true);
    const pos = (n) => { const b = this.bone(n); return b ? b.getWorldPosition(new THREE.Vector3()) : null; };
    const inv = this.root.getWorldQuaternion(new THREE.Quaternion()).invert();
    this._invRoot = inv;
    const dir = (from, to) => to.sub(from).normalize().applyQuaternion(inv);
    this.rest = {};
    for (const s of SIDES) {
      const P = s.pre;
      const R = this.rest[P] = {
        upper: dir(pos(P + "UpperArm"), pos(P + "LowerArm")),
        lower: dir(pos(P + "LowerArm"), pos(P + "Hand")),
        handF: dir(pos(P + "Hand"), pos(P + "MiddleProximal")),
        handS: dir(pos(P + "IndexProximal"), pos(P + "LittleProximal")),
        fingers: FINGERS.map(([names]) => names.map((n, i) => {
          const a = pos(P + n), b = i < 2 ? pos(P + names[i + 1]) : null;
          return a && b ? dir(a, b) : null;
        })),
      };
      R.fingers.forEach((f) => { f[2] = f[1] ? f[1].clone() : null; });
      // 손바닥 방향(기본 자세): T자세 VRM 은 아래, A자세 모델은 기울어 있음 → 손 두 축으로 계산
      R.palm = new THREE.Vector3().crossVectors(R.handF, R.handS).normalize().multiplyScalar(P === "left" ? -1 : 1);
      R.sho = pos(P + "UpperArm");
      R.clav = pos(P + "Shoulder");
      R.a = pos(P + "LowerArm").distanceTo(R.sho);
      R.b = pos(P + "Hand").distanceTo(pos(P + "LowerArm"));
      R.handLen = pos(P + "Hand").distanceTo(pos(P + "MiddleDistal") || pos(P + "MiddleProximal")) + 0.025;
      // 손가락 '바깥쪽'(가운데 손가락에서 멀어지는 방향) — 손가락끼리 겹치지 않게 벌림 제한할 때
      const mid = pos(P + "MiddleProximal");
      R.out = FINGERS.map(([names]) => { const b = pos(P + names[0]); return b && mid ? dir(mid, b.clone()) : new THREE.Vector3(); });
      // CC4 팔뚝 비틀림 뼈: 기본 회전과 뼈 축(로컬)
      R.twist = ((this.vrm.twist || {})[P] || []).map((tb) => {
        const child = tb.children.find((c) => c.isBone) || this.raw(P + "Hand");
        const wp = tb.getWorldPosition(new THREE.Vector3()), cp = child.getWorldPosition(new THREE.Vector3());
        const axis = cp.sub(wp).applyQuaternion(tb.getWorldQuaternion(new THREE.Quaternion()).invert()).normalize();
        return { bone: tb, rest: tb.quaternion.clone(), axis };
      });
    }
    this.headY = pos("head").y;
    this._modelTop = new THREE.Box3().setFromObject(this.root).max.y;
    this.neckY = pos("neck").y;
    this.neckPos = pos("neck");
    this._hipsY = pos("hips").y;
    this.spinePos = pos("chest") || pos("spine");
    this.shoulderW = pos("leftUpperArm").distanceTo(pos("rightUpperArm"));
    const eye = pos("leftEye") || pos("head").add(new THREE.Vector3(0, 0.08, 0.05));
    this.eyeY = eye.y;
    this.noseAbove = (eye.y - 0.025 - this.neckY) / Math.max(this.shoulderW, 1e-6);
    const skull = this._skull();
    this.headCenter = skull ? new THREE.Vector3(skull.cx, eye.y - 0.01, skull.cz) : new THREE.Vector3(pos("head").x, eye.y - 0.01, pos("head").z);
    this.headR = skull ? Math.max(skull.rx, skull.rz * 0.9) * 1.04 : this.shoulderW * 0.36;
    this._skullBox = skull;
    const ls = this.raw("leftShoulder") || this.raw("leftUpperArm");
    this._neckCarriesArms = !!(ls && this.raw("neck") && ls.parent === this.raw("neck"));
    // 시선: 눈 뼈 기본 회전
    this.eyes = ["leftEye", "rightEye"].map((n) => this.bone(n)).filter(Boolean);
  }

  // 머리통 크기: 머리 뼈를 가장 많이 따르는 꼭짓점들의 범위 (반실사 GLB 만; VRM 은 예전 방식)
  _skull() {
    if (this.vrm.isVRM) return null;
    const head = this.raw("head");
    if (!head) return null;
    const under = new Set();
    head.traverse((o) => under.add(o));
    const box = new THREE.Box3(), v = new THREE.Vector3();
    this.root.traverse((o) => {
      if (!o.isSkinnedMesh || /helmet|hat|cap/i.test([].concat(o.material).map((m) => m.name).join(" "))) return;   // 모델에 있는 안전모는 빼고 머리통만
      const sk = o.skeleton, idx = o.geometry.attributes.skinIndex, w = o.geometry.attributes.skinWeight, P = o.geometry.attributes.position;
      if (!idx || !w) return;
      for (let i = 0; i < P.count; i += 2) {
        let best = 0, bi = -1;
        for (let k = 0; k < 4; k++) { const ww = w.getComponent(i, k); if (ww > best) { best = ww; bi = idx.getComponent(i, k); } }
        const bone = sk.bones[bi];
        if (!bone || !under.has(bone) || /pony|tail|braid/i.test(bone.name)) continue;
        o.getVertexPosition(i, v); v.applyMatrix4(o.matrixWorld);
        box.expandByPoint(v);
      }
    });
    if (box.isEmpty()) return null;
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
    return { cx: c.x, cz: c.z, rx: s.x / 2, rz: s.z / 2, top: box.max.y, min: box.min, max: box.max };
  }

  // 안전모 (반실사 재질): 머리통 크기에 맞춰 이마를 덮도록, 머리 뼈를 따라 움직임
  _hardHat() {
    const head = this.raw("head"), sk = this._skullBox;
    if (!head || !sk) return;
    const brow = this.eyeY + 0.035;
    const R = sk.rx + 0.022, zs = (sk.rz + 0.022) / R;
    const Hh = Math.max(R * 0.72, sk.top - brow + 0.02);
    const shellMat = new THREE.MeshPhysicalMaterial({ color: 0xf2b200, roughness: 0.38, metalness: 0, clearcoat: 0.6, clearcoatRoughness: 0.25 });
    const brimMat = shellMat.clone(); brimMat.side = THREE.DoubleSide;
    const pts = [];
    for (let i = 0; i <= 28; i++) { const t = (i / 28) * Math.PI / 2; pts.push(new THREE.Vector2(Math.max(R * Math.cos(t), 0.0005), Hh * Math.pow(Math.sin(t), 0.8))); }
    const hat = new THREE.Group();
    const shell = new THREE.Mesh(new THREE.LatheGeometry(pts, 72), shellMat); shell.scale.set(1, 1, zs);
    const visor = new THREE.Mesh(new THREE.LatheGeometry([
      new THREE.Vector2(R * 0.98, 0.006), new THREE.Vector2(R * 1.22, -0.01), new THREE.Vector2(R * 1.22, -0.015), new THREE.Vector2(R * 0.98, -0.003),
    ], 48, -Math.PI * 0.39, Math.PI * 0.78), brimMat);
    visor.scale.set(1, 1, zs);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(R * 1.01, 0.006, 10, 72), shellMat); lip.rotation.x = Math.PI / 2; lip.scale.set(1, zs, 1);
    const rib = new THREE.Mesh(new THREE.TorusGeometry(R * 0.995, R * 0.03, 8, 48, Math.PI * 0.7), shellMat);
    rib.rotation.set(0, Math.PI / 2, Math.PI * 0.15); rib.scale.set(zs, Hh / R * 0.98, 1);
    hat.add(shell, visor, lip, rib);
    hat.position.set(sk.cx, brow, sk.cz);
    hat.rotation.x = 0.05;
    this.scene.add(hat);
    hat.updateMatrixWorld(true);
    head.attach(hat);
    this._hat = hat;
  }

  frontZ(y) {
    let best = -Infinity;
    for (const [yy, z] of this.front) if (Math.abs(yy - y) <= 0.05) best = Math.max(best, z);
    return best;
  }

  // 몸통 앞면이 높이(y)마다 z 어디까지 나와 있는지 측정 (머리카락 제외, 기본 자세라 팔은 안 걸림)
  _measureBodyFront(y0, y1) {
    this.vrm.update(0);
    this.root.updateMatrixWorld(true);
    const step = 0.02, n = Math.max(1, Math.round((y1 + 0.02 - (y0 - 0.1)) / step) + 1);
    const zs = new Array(n).fill(-Infinity), base = y0 - 0.1;
    const v = new THREE.Vector3(), half = this.shoulderW * 0.36;
    this.root.traverse((o) => {
      if (!o.isMesh || !o.visible) return;
      const mats = [].concat(o.material || []).map((m) => m.name).join(" ");
      if (/hair|pony|lash|brow/i.test(o.name + " " + mats)) return;
      const pos = o.geometry.attributes.position;
      for (let k = 0; k < pos.count; k++) {
        if (o.isSkinnedMesh) o.getVertexPosition(k, v); else v.fromBufferAttribute(pos, k);
        v.applyMatrix4(o.matrixWorld);
        if (Math.abs(v.x) > half) continue;
        const i = Math.round((v.y - base) / step);
        if (i >= 0 && i < n && v.z > zs[i]) zs[i] = v.z;
      }
    });
    this.front = zs.map((z, i) => [base + i * step, z]);
    this.torsoHalf = this.shoulderW * 1.0;
  }

  // 2관절 IK: 어깨 S, 목표 손목 W, 팔 길이 a·b, 팔꿈치가 향할 방향 pole
  _ik(S, W, a, b, pole) {
    const d0 = W.clone().sub(S);
    const d = Math.min(d0.length(), a + b - 1e-4);
    const dir = d0.normalize();
    const along = (a * a - b * b + d * d) / (2 * d);
    const h = Math.sqrt(Math.max(0, a * a - along * along));
    const perp = pole.clone().sub(dir.clone().multiplyScalar(pole.dot(dir)));
    if (perp.lengthSq() < 1e-8) perp.set(0, -1, 0);
    perp.normalize();
    const E = S.clone().add(dir.clone().multiplyScalar(along)).add(perp.multiplyScalar(h));
    return [E, S.clone().add(dir.multiplyScalar(d))];
  }

  // 화면: 안전모 위 ~ 배까지
  _frameCamera() {
    // 위: 씌운 안전모 끝, 없으면 모델 맨 위(모델에 있는 안전모·머리) / 아래: 골반 위 (config.frameBottom 로 조절)
    const top = this._hat ? new THREE.Box3().setFromObject(this._hat).max.y : (this._modelTop ?? this.headY + 0.2);
    const bottom = this._hipsY + ((this.config && this.config.frameBottom) ?? 0.04);
    const pad = 0.05, span = top - bottom + pad;
    const half = D(this.camera.fov / 2);
    const dist = span / 2 / Math.tan(half);
    const cy = bottom + span / 2;
    this.camera.position.set(0, cy + 0.02, dist);
    this.camera.lookAt(0, cy, 0);
  }

  _resize() {
    const w = this.canvas.clientWidth || 400, h = this.canvas.clientHeight || 400;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // 부모 회전을 이어받은 상태에서 restDir → target 으로 돌리는 '로컬' 회전
  _aim(bone, restDir, target, parentWorld) {
    const cur = restDir.clone().applyQuaternion(parentWorld);
    const swing = new THREE.Quaternion().setFromUnitVectors(cur, target.clone().normalize());
    const world = swing.multiply(parentWorld);
    const local = parentWorld.clone().invert().multiply(world);
    this._set(bone, local);
    return world;
  }

  _set(bone, q) {
    const b = typeof bone === "string" ? this.bone(bone) : bone;
    if (!b) return;
    b.userData.target = q;
    b.userData.tau = tauFor(typeof bone === "string" ? bone : b.name);
    this.driven.add(b);
  }

  // 월드 기준 회전 → 모델(정규화 뼈대) 기준. 모델이 -z 를 보고 180° 돌려 놓은 경우(VRM0 등)에도 같은 방향으로 움직이게
  _W2M(q) {
    const I = this.root.getWorldQuaternion(new THREE.Quaternion());
    return I.clone().invert().multiply(q).multiply(I);
  }

  // 손가락 관절 범위: 추정 좌표 떨림으로 손등 쪽으로 꺾이거나 옆으로 비틀리는 것 방지
  //  일반 손가락의 굽힘은 '데이터의 마디 사이 각도'(flexData)로 정함 → 손바닥 방향 추정이 틀어져도 손가락이 갈고리처럼 꺾이지 않음
  //  굽힘 범위 (뿌리 0°~90°, 가운데 0°~100°, 끝 0°~75°), 벌림은 뿌리만 (바깥 12° / 안쪽 6°: 옆 손가락과 겹치지 않게)
  //  엄지: 뿌리(손바닥 안 마디)는 원뿔 안 60°, 가운데·끝은 손바닥 쪽으로만 굽힘
  //  끝마디는 가운데 마디의 2/3 정도로 연동 (사람 손가락 힘줄 구조)
  _fingerTarget(R, fi, j, target, parentW, prior, prev, flexData = null) {
    const restDir = R.fingers[fi][j];
    const p = restDir.clone().applyQuaternion(parentW).normalize();
    const t = target.clone().normalize();
    if (fi === 0 && j === 0) {                                   // 엄지 뿌리: 원뿔 제한
      const ang = p.angleTo(t), lim = D(60);
      if (ang <= lim) return t;
      const axis = new THREE.Vector3().crossVectors(p, t).normalize();
      return p.applyAxisAngle(axis, lim);
    }
    const palm = R.palm.clone().applyQuaternion(parentW);                     // 손바닥 쪽
    let n = palm.clone();
    if (fi === 0) n.multiplyScalar(0.6).add(R.out[1].clone().negate().applyQuaternion(this._handWQ).multiplyScalar(0.8));   // 엄지는 손바닥·새끼손가락 쪽으로 감김
    n.sub(p.clone().multiplyScalar(n.dot(p))).normalize();
    const l = new THREE.Vector3().crossVectors(p, n);
    let flex = flexData != null ? flexData : Math.atan2(t.dot(n), t.dot(p));
    let side = Math.asin(clamp(t.dot(l), -1, 1));
    const MAX = fi === 0 ? [0, D(70), D(85)] : [D(90), D(100), D(75)];
    const MIN = fi === 0 ? [0, D(-5), D(-10)] : [0, 0, 0];
    flex = clamp(flex, MIN[j], MAX[j]);
    if (prior) flex += (prior.val - flex) * prior.w;                         // 자주 나오는 손 모양 쪽으로
    if (j === 2 && prev != null && fi > 0) flex = flex * 0.5 + prev * 0.67 * 0.5;   // 끝마디 연동
    if (j === 0 && fi > 0) {
      const outSign = Math.sign(R.out[fi].clone().applyQuaternion(this._handWQ).dot(l)) || 1;
      const lo = fi === 2 ? -D(6) : -D(6), hi = fi === 2 ? D(6) : D(12);
      const s = side * outSign;
      side = clamp(s, lo, hi) * outSign;
    } else side = 0;
    const dir = p.clone().multiplyScalar(Math.cos(flex)).add(n.clone().multiplyScalar(Math.sin(flex))).multiplyScalar(Math.cos(side))
      .add(l.multiplyScalar(Math.sin(side)));
    dir.flex = flex;
    return dir;
  }

  // 손바닥 두 축으로 손목 전체 회전
  _basis(f, s) {
    const x = f.clone().normalize();
    const z = new THREE.Vector3().crossVectors(x, s).normalize();
    const y = new THREE.Vector3().crossVectors(z, x).normalize();
    return new THREE.Matrix4().makeBasis(x, y, z);
  }

  // fr: 동작 한 프레임, word: { gloss, t, nms, text }, m: 동작 전체 (단어 사이 연결 구간을 회전으로 섞을 때)
  setFrame(fr, word = null, m = null) {
    if (!this.vrm) return;
    this._face = fr;
    this._word = fr ? word : null;
    if (fr && fr.tr && m && m.frames[fr.tr.a] && m.frames[fr.tr.b]) {
      const A = this._poseSnap(m, fr.tr.a), B = this._poseSnap(m, fr.tr.b), t = minJerk(clamp(fr.tr.t, 0, 1));
      for (const [o, q] of A.q) {
        const qb = B.q.get(o);
        o.userData.target = qb ? q.clone().slerp(qb, t) : q.clone();
        this.driven.add(o);
      }
      for (const P of ["left", "right"]) this.st.twist[P] = A.tw[P] + (B.tw[P] - A.tw[P]) * t;
      return;
    }
    const w = fr && fr.rest > 0 ? Math.min(1, fr.rest) : 0;
    this._pose(fr);
    if (!w) return;
    // 시작·끝: 동작 자세와 기본 자세(팔 내림)를 관절마다 섞음 → 손이 자연스럽게 올라오고 내려감
    const a = new Map([...this.driven].map((o) => [o, o.userData.target.clone()]));
    const tw = { ...this.st.twist };
    this._pose(null);
    for (const [o, q] of a) o.userData.target = q.slerp(o.userData.target, w);
    for (const P of ["left", "right"]) this.st.twist[P] = tw[P] * (1 - w);
  }

  // 연결 구간용: 그 프레임의 자세(뼈 회전)를 한 번만 계산해 저장 (시간 상태는 건드리지 않음)
  _poseSnap(m, i) {
    if (!this._snaps || this._snaps.frames !== m.frames) this._snaps = { frames: m.frames, map: new Map() };
    let s = this._snaps.map.get(i);
    if (!s) {
      const keep = this.st;
      this.st = { pole: {}, hand: {}, twist: { left: 0, right: 0 }, pure: true };
      this._pose(m.frames[i]);
      s = { q: new Map([...this.driven].map((o) => [o, o.userData.target.clone()])), tw: { ...this.st.twist } };
      this.st = keep;
      this._snaps.map.set(i, s);
    }
    return s;
  }

  // 몸통 회전(모델 좌표): 데이터 어깨선 기울기·회전 + 손을 앞으로 뻗으면 살짝 숙임 + 숨쉬기
  _torso(fr) {
    let roll = 0, yaw = 0, pitch = 0;
    const b = fr && fr.body;
    if (b && b[2] && b[5]) {
      const d = V(b[5]).sub(V(b[2]));
      roll = clamp(Math.atan2(d.y, d.x) * 0.7, -D(6), D(6));
      yaw = clamp(-Math.atan2(d.z, d.x) * 0.6, -D(12), D(12));
      const fz = Math.max(b[4] ? -(b[4][2] || 0) : 0, b[7] ? -(b[7][2] || 0) : 0);   // 손목이 몸보다 앞 (z 클수록 카메라 쪽)
      pitch = clamp((fz - 0.5) * 0.12, 0, D(4));
    }
    pitch += D(0.6) * Math.sin(performance.now() / 1000 * 2 * Math.PI / 4.2);   // 숨쉬기
    return { roll, yaw, pitch };
  }

  _pose(fr) {
    const neutral = !fr || !fr.body;
    const I = this.root.getWorldQuaternion(new THREE.Quaternion());
    // ---- 몸통: 척추·가슴·윗가슴에 나눠서
    const T = this._torso(neutral ? null : fr);
    this._torsoYaw = T.yaw;
    const parts = ["spine", "chest", "upperChest"].filter((n) => this.bone(n));
    const share = parts.length === 3 ? [0.35, 0.35, 0.3] : parts.length === 2 ? [0.5, 0.5] : [1];
    const tq = (k) => this._W2M(new THREE.Quaternion().setFromEuler(new THREE.Euler(T.pitch * k, T.yaw * k, T.roll * k, "YXZ")));
    const chestQ = new THREE.Quaternion();
    parts.forEach((n, i) => { const q = tq(share[i]); this._set(n, q); chestQ.multiply(q); });
    const chestW = I.clone().multiply(chestQ);
    const chestWorldRot = I.clone().multiply(chestQ).multiply(I.clone().invert());        // 월드에서 몸통 회전
    const rotAbout = (p, pivot, q) => p.clone().sub(pivot).applyQuaternion(q).add(pivot);

    for (const s of SIDES) {
      const P = s.pre, R = this.rest[P], sign = P === "left" ? 1 : -1;
      const b = fr && fr.body;
      let upperW, lowerW;
      let clavW = chestW;
      if (!neutral && b[s.sho] && b[s.elb] && b[s.wri]) {
        const sh = V(b[s.sho]), el = V(b[s.elb]), wr = V(b[s.wri]);
        // 손목 목표 = 목 위치 + (데이터의 목→손목) × 아바타 어깨너비 (얼굴 높이는 코 높이 비율로)
        const off = wr.clone();
        const dn = b[0] ? -b[0][1] : 0;
        if (off.y > 0 && dn > 0.1) off.y *= Math.min(2.2, Math.max(0.8, this.noseAbove / dn));
        const W = this.neckPos.clone().add(off.multiplyScalar(this.shoulderW));
        // 얼굴 안으로 들어가면 얼굴 앞쪽으로
        const fromHead = W.clone().sub(this.headCenter);
        if (fromHead.length() < this.headR) {
          fromHead.z = Math.max(fromHead.z, 0.02);
          W.copy(this.headCenter).add(fromHead.normalize().multiplyScalar(this.headR));
        }
        const hd = fr[s.hand];
        if (hd && hd[0]) {
          for (const tipIdx of [8, 12]) {                                         // 손가락 끝이 얼굴을 뚫지 않게
            if (!hd[tipIdx]) continue;
            const dir = V(hd[tipIdx]).sub(V(hd[0])).normalize();
            const tip = W.clone().add(dir.multiplyScalar(R.handLen));
            const ft = tip.clone().sub(this.headCenter), d = ft.length(), need = this.headR * 1.02 - d;
            if (need > 0) { ft.z = Math.max(ft.z, 0.02); W.add(ft.normalize().multiplyScalar(need)); }
          }
          for (const [tipIdx, frac] of [[12, 1], [9, 0.55], [8, 1], [4, 0.8]]) {   // 손끝이 몸통 안으로 들어가지 않게
            if (!hd[tipIdx]) continue;
            const dir = V(hd[tipIdx]).sub(V(hd[0])).normalize();
            const tip = W.clone().add(dir.multiplyScalar(R.handLen * frac));
            if (Math.abs(tip.x) > this.torsoHalf * 1.05 || tip.y < this._hipsY) continue;
            const fz = this.frontZ(tip.y);
            if (isFinite(fz) && tip.z < fz + 0.015) W.z += fz + 0.015 - tip.z;
          }
        }
        if (Math.abs(W.x) < this.torsoHalf && W.y > this._hipsY) {
          const fz = this.frontZ(W.y);
          if (isFinite(fz) && W.z < fz + this.margin) W.z = fz + this.margin;
        }
        // ---- 쇄골: 손을 어깨보다 높이 들면 들어 올리고, 앞으로 뻗거나 몸 반대쪽으로 넘기면 앞으로 내밈
        const shoW = rotAbout(R.sho, this.spinePos, chestWorldRot);
        const raise = clamp((W.y - (shoW.y - 0.05)) / 0.3, 0, 1) * D(10);
        const reach = clamp((W.z - shoW.z - 0.22) / 0.25, 0, 1) * D(6) + clamp((-sign * W.x) / (this.shoulderW * 0.6), 0, 1) * D(6);
        const clavQ = this._W2M(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, -sign * reach, sign * raise, "YXZ")));
        let sho2 = shoW;
        if (this.bone(P + "Shoulder") && R.clav) {
          this._set(P + "Shoulder", clavQ);
          clavW = chestW.clone().multiply(clavQ);
          const clavPos = rotAbout(R.clav, this.spinePos, chestWorldRot);
          const clavWorldRot = I.clone().multiply(chestQ).multiply(clavQ).multiply(I.clone().invert());
          sho2 = R.sho.clone().sub(R.clav).applyQuaternion(clavWorldRot).add(clavPos);
        }
        // ---- 팔꿈치 방향: 데이터 70% + 기본(아래·바깥·뒤) 30%, 위로 들리지 않게, 시간에 따라 부드럽게
        const raw = el.clone().sub(sh).normalize();
        const def = new THREE.Vector3(sign * 0.35, -1, -0.3).normalize();
        let pole = raw.multiplyScalar(0.7).add(def.multiplyScalar(0.3));
        pole.y = Math.min(pole.y, -0.2);
        pole.normalize();
        const prevPole = this.st.pole[P];
        if (prevPole && !this.st.pure) pole = prevPole.clone().lerp(pole, 1 - Math.exp(-this._dt / 0.12)).normalize();
        this.st.pole[P] = pole;
        let [E, W2] = this._ik(sho2, W, R.a, R.b, pole);
        // 팔꿈치가 몸통 안으로 들어가면(손을 몸 반대쪽으로 넘길 때 흔함) 팔꿈치 방향을 바깥쪽으로 돌려 다시 계산
        for (let k = 0; k < 3; k++) {
          const fz = this.frontZ(E.y);
          if (!(Math.abs(E.x) < this.shoulderW * 0.42 && E.y > this._hipsY && isFinite(fz) && E.z < fz + 0.02)) break;
          pole = pole.clone().add(new THREE.Vector3(sign * 0.6, 0, 0.25)).normalize();
          [E, W2] = this._ik(sho2, W, R.a, R.b, pole);
        }
        upperW = this._aim(P + "UpperArm", R.upper, E.clone().sub(sho2), clavW);
        lowerW = this._aim(P + "LowerArm", R.lower, W2.clone().sub(E), upperW);
      } else {
        this._set(P + "Shoulder", new THREE.Quaternion());
        const down = new THREE.Vector3(sign * 0.16, -1, 0.06);
        upperW = this._aim(P + "UpperArm", R.upper, down, chestW);
        lowerW = this._aim(P + "LowerArm", R.lower, new THREE.Vector3(sign * 0.05, -1, 0.3), upperW);
      }
      this._hand(fr, s, R, lowerW, upperW, neutral);
    }
  }

  // 손목 + 손가락. 손이 잠깐 안 잡힌 프레임은 0.5초까지 마지막 손 모양 유지, 그 뒤엔 힘 뺀 손
  _hand(fr, s, R, lowerW, upperW, neutral) {
    const P = s.pre;
    let h = fr && fr[s.hand];
    const okHand = (x) => x && x[0] && x[9] && x[5] && x[17];
    const mem = this.st.hand[P];
    if (okHand(h)) this.st.hand[P] = { h, age: 0 };
    else if (!neutral && mem && mem.age < 0.5) { h = mem.h; if (!this.st.pure) mem.age += this._dt || 0.033; }
    else h = null;
    if (!okHand(h)) {
      this._set(P + "Hand", new THREE.Quaternion());
      this.st.twist[P] = 0;
      FINGERS.forEach(([names], fi) => names.forEach((n, j) => {     // 힘 뺀 손: 살짝 굽힘
        const curl = fi === 0 ? [0, 10, 10][j] : [12 + fi * 3, 18 + fi * 3, 10][j];
        const d = R.fingers[fi][j];
        if (d) this._set(P + n, new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3().crossVectors(d, R.palm).normalize(), D(curl) * (fi === 0 ? 0.3 : 1)));
      }));
      return;
    }
    const f = V(h[9]).sub(V(h[0])), sd = V(h[17]).sub(V(h[5]));
    const M = this._basis(f, sd), M0 = this._basis(R.handF, R.handS);
    const handW = new THREE.Quaternion().setFromRotationMatrix(M.multiply(M0.invert()));
    // ---- 손목 비틀림 나누기: 손목 회전 중 아래팔 축 둘레 비틀림의 45% 를 아래팔에 (CC4 비틀림 뼈가 있으면 그 뼈에)
    let local = lowerW.clone().invert().multiply(handW);
    const axis = R.lower.clone();
    const tw = new THREE.Vector3(local.x, local.y, local.z).dot(axis);
    let theta = 2 * Math.atan2(tw, local.w);
    if (theta > Math.PI) theta -= 2 * Math.PI; if (theta < -Math.PI) theta += 2 * Math.PI;
    let lowerW2 = lowerW;
    if (R.twist.length) this.st.twist[P] = theta * 0.6;
    else {
      lowerW2 = lowerW.clone().multiply(new THREE.Quaternion().setFromAxisAngle(axis, theta * 0.45));
      this._set(P + "LowerArm", upperW.clone().invert().multiply(lowerW2));
      this.st.twist[P] = 0;
    }
    local = lowerW2.clone().invert().multiply(handW);
    // ---- 손목 꺾임 한계: 60° (손 데이터의 손목과 팔 데이터의 손목이 많이 어긋나면 손 추정이 불확실 → 40°)
    //  넘으면 '꺾임'만 줄이고 비틀림은 그대로. 손이 축 처지거나 뒤로 젖혀져 보이던 문제
    const bw = fr.body && fr.body[s.wri];
    const gap = bw && h[0] ? Math.hypot(bw[0] - h[0][0], bw[1] - h[0][1]) : 0;
    const maxBend = D(gap > 0.08 ? 40 : 60);
    const cur = R.handF.clone().applyQuaternion(local).normalize();
    const bend = R.handF.angleTo(cur);
    if (bend > maxBend) {
      const swing = new THREE.Quaternion().setFromUnitVectors(R.handF, cur);
      const twistQ = swing.clone().invert().multiply(local);
      const ax = new THREE.Vector3().crossVectors(R.handF, cur).normalize();
      local = new THREE.Quaternion().setFromAxisAngle(ax, maxBend).multiply(twistQ);
    }
    const handW2 = lowerW2.clone().multiply(local);
    this._set(P + "Hand", local);
    this._handWQ = handW2;
    // ---- 손가락: 데이터의 마디 사이 각도 - 편 상태 기준(handshapes.base)
    const x = this._handAngles(h);
    const base = (this.handshapes && this.handshapes.base) || new Array(15).fill(10);
    const prior = x ? this._handPrior(x) : null;
    FINGERS.forEach(([names, idx], fi) => {
      let parentW = handW2, prevFlex = null;
      names.forEach((n, j) => {
        const a = h[idx[j]], c = h[idx[j + 1]];
        if (!a || !c || !R.fingers[fi][j]) return;
        const k = fi * 3 + j;
        const pr = prior && fi > 0 ? { val: D(Math.max(0, prior.c[k] - base[k])), w: prior.w } : null;
        const fd = x && fi > 0 ? D(Math.max(0, x[k] - base[k])) : null;
        const t = this._fingerTarget(R, fi, j, V(c).sub(V(a)), parentW, pr, prevFlex, fd);
        prevFlex = t.flex ?? null;
        parentW = this._aim(P + n, R.fingers[fi][j], t, parentW);
      });
    });
  }

  // 자주 나오는 손 모양(web/handshapes.json, 동작 데이터 군집): 가장 가까운 모양 쪽으로 최대 35% 끌어당김
  // 손가락 15마디 굽힘 각도(도): 앞 마디 방향과 이번 마디 방향 사이 각 (scripts/data/build_handshapes.py 와 같은 계산)
  _handAngles(h) {
    const ang = (a, b, c) => {
      if (!a || !b || !c) return null;
      const u = V(b).sub(V(a)), v = V(c).sub(V(b));
      return THREE.MathUtils.radToDeg(u.angleTo(v));
    };
    const x = [];
    for (const [, idx] of FINGERS) {
      const chain = [0, ...idx];
      for (let j = 0; j < 3; j++) x.push(ang(h[chain[j]], h[chain[j + 1]], h[chain[j + 2]]));
    }
    return x.some((v) => v == null) ? null : x;
  }

  _handPrior(x) {
    const S = this.handshapes;
    if (!S || !S.centers || !x) return null;
    let best = null, bd = Infinity;
    for (const c of S.centers) {
      let d = 0;
      for (let i = 0; i < 15; i++) d += (x[i] - c[i]) ** 2;
      if (d < bd) { bd = d; best = c; }
    }
    const rms = Math.sqrt(bd / 15);
    const w = 0.35 * Math.exp(-((rms / (S.scale || 15)) ** 2));
    return w > 0.02 ? { c: best, w } : null;
  }

  // 얼굴·고개·시선 (매 화면마다)
  _applyFace(fr, dt) {
    const f = this.face;
    const e = (fr && fr.expr) || {};
    const w = this._word, nm = w && mergeNMS(NMS[w.gloss], w.nms);
    const t = w ? clamp(w.t, 0, 1) : 0;
    const env = nm ? Math.min(1, t / 0.18, (1 - t) / 0.2 + 0.35) : 0;
    const now = performance.now();
    // 입 모양: 단어의 한국어를 입으로 (있으면 데이터 입 모양은 약하게)
    let mouthing = false;
    if (w && w.text) {
      if (this._sylText !== w.text) { this._sylText = w.text; this._syl = mouthSyllables(w.text); }
      const mo = mouthAt(this._syl, t, 0.4);
      for (const [k, v] of Object.entries(mo)) { f.set(k, v); mouthing = true; }
    }
    // 수어자 얼굴 데이터 (입 모양·눈 깜빡임·눈썹) → ARKit
    const mk = mouthing ? 0.15 : 0.45;
    f.set("jawOpen", (e.aa || 0) * mk);
    f.set("mouthPucker", (e.ou || 0) * mk * 1.2);
    f.set("mouthStretchLeft", (e.ee || 0) * mk); f.set("mouthStretchRight", (e.ee || 0) * mk);
    for (const [k, gain] of [["surprised", 0.45], ["angry", 0.4]]) {
      for (const [name, v] of Object.entries(FACE_ARKIT[k])) f.set(name, (e[k] || 0) * gain * v);
    }
    // 단어·문법 표정
    if (nm) for (const [k, v] of Object.entries(nm.face || {})) {
      for (const [name, s] of Object.entries(FACE_ARKIT[k] || {})) f.set(name, v * s * env);
    }
    // 눈 깜빡임: 데이터 + 평소(2.5~5초) + 고개를 크게 돌린 직후
    if (!this._nextBlink) this._nextBlink = now + 3000;
    const surprised = nm && (nm.face?.surprised || 0) > 0.4;
    let blink = (e.blink || 0) * 0.8;
    if (now > this._nextBlink) { blink = 1; if (now > this._nextBlink + 130) this._nextBlink = now + 2500 + Math.random() * 2500; }
    if (surprised) blink *= 0.3;
    f.set("eyeBlinkLeft", blink); f.set("eyeBlinkRight", blink);
    f.apply(dt);

    // ---- 고개: 데이터 각도(±14°) + 단어 움직임 → 스프링 → 목 40% · 머리 60%. 몸통 회전의 60% 는 되돌려 얼굴이 정면을 보게
    const hd = ((fr && fr.head) || [0, 0, 0]).map((v) => clamp(v, -14, 14));
    const mv = nm ? headMove(nm.head, t) : [0, 0, 0];
    const tgt = [hd[0] + mv[0] - THREE.MathUtils.radToDeg(this._torsoYaw || 0) * 0.6, -hd[1] + mv[1], hd[2] + mv[2]];   // [좌우, 아래(+), 기울기]
    const prevYaw = this.headSpring.x[0];
    const x = this.headSpring.step(tgt, dt);
    if (Math.abs(x[0] - prevYaw) / Math.max(dt, 1e-3) > 60 && now > this._nextBlink - 2000) this._nextBlink = now + 60;   // 큰 고개 돌림 뒤 깜빡
    const qOf = (k) => this._W2M(new THREE.Quaternion().setFromEuler(new THREE.Euler(D(x[1]) * k, D(x[0]) * k, D(x[2]) * k, "YXZ")));
    // Rocketbox(3ds Max Biped) 처럼 쇄골이 목에 붙은 뼈대는 목을 돌리면 팔까지 흔들림 → 고개 회전은 머리 뼈에만
    if (this._neckCarriesArms) { this._setNow("neck", new THREE.Quaternion()); this._setNow("head", qOf(1)); }
    else { this._setNow("neck", qOf(0.4)); this._setNow("head", qOf(0.6)); }

    // ---- 시선: 보통은 보는 사람(카메라), 단어 중 손이 얼굴 앞으로 오면 살짝 손 쪽. 가끔 아주 작은 눈 움직임
    if (!this._sacc || now > this._sacc.until) this._sacc = { x: (Math.random() - 0.5) * 2.4, y: (Math.random() - 0.5) * 1.6, until: now + 800 + Math.random() * 1400 };
    let gx = this._sacc.x, gy = this._sacc.y;
    const rh = fr && fr.body && fr.body[4];
    if (w && rh && -rh[1] > -0.3) { gx += clamp(rh[0] * 10, -8, 8) * 0.4; gy += clamp(-rh[1] * 8, -6, 6) * 0.4; }   // 오른손(화면 왼쪽) 쪽
    gx -= x[0] * 0.5; gy += x[1] * 0.5;                                              // 고개를 돌려도 눈은 카메라 쪽에 남게 (gy + = 위)
    const g = this.gazeSpring.step([clamp(gx, -15, 15), clamp(gy, -10, 10)], dt);
    if (this.vrm.isVRM) {
      if (this._gazeObj) this._gazeObj.position.copy(this.camera.position).add(new THREE.Vector3(Math.tan(D(g[0])), Math.tan(D(g[1])), 0).multiplyScalar(this.camera.position.z));
    } else {
      const q = this._W2M(new THREE.Quaternion().setFromEuler(new THREE.Euler(D(-g[1]), D(g[0]), 0, "YXZ")));
      for (const eye of this.eyes) eye.quaternion.copy(q);
    }
  }

  _setNow(name, q) {     // 스프링으로 이미 부드럽게 한 값: 바로 반영
    const b = this.bone(name);
    if (!b) return;
    b.userData.target = q; b.userData.tau = 0; b.quaternion.copy(q);
    this.driven.add(b);
  }

  // CC4 팔뚝 비틀림 뼈: 사람 뼈대 밖이라 원래 뼈에 직접 (손목 쪽일수록 많이)
  _applyTwist() {
    for (const P of ["left", "right"]) {
      const list = this.rest[P].twist;
      if (!list.length) continue;
      const th = this.st.twist[P] || 0;
      list.forEach((tb, i) => {
        const k = (i + 1) / list.length;
        tb.bone.quaternion.copy(tb.rest).multiply(new THREE.Quaternion().setFromAxisAngle(tb.axis, th * k));
      });
    }
  }

  _loop() {
    const tick = () => {
      requestAnimationFrame(tick);
      const now = performance.now(), dt = Math.min(0.1, (now - this.last) / 1000);
      this.last = now;
      this._dt = dt;
      if (!this._face) this._pose(null);                 // 쉬는 동안에도 숨쉬기
      for (const o of this.driven) {
        const tau = o.userData.tau ?? 0.05;
        if (tau > 0) o.quaternion.slerp(o.userData.target, 1 - Math.exp(-dt / tau));
      }
      this._applyFace(this._face, dt);
      this.vrm.update(dt);
      this._applyTwist();
      this.renderer.render(this.scene, this.camera);
    };
    tick();
  }
}

// 예전 VRM 아바타 꾸미기 메서드 붙이기 (?avatar=vrm)
for (const k of Object.getOwnPropertyNames(VRMLook.prototype)) {
  if (k !== "constructor") Object.defineProperty(SignAvatar.prototype, k, Object.getOwnPropertyDescriptor(VRMLook.prototype, k));
}
