// 3D 수어 아바타 (Three.js + VRM)
// 동작 프레임(목=원점, 어깨너비=1, x 오른쪽 · y 아래 · z 카메라에서 멀어지는 쪽)을
// VRM 정규화 뼈대의 회전값으로 바꿔 재생한다.
//
// 원리: 정규화 뼈대는 T자세에서 모든 뼈 회전이 0 → 각 뼈의 '기본 방향'은 T자세 뼈 위치로 구함.
//   팔·손가락: 부모 회전을 이어받은 상태에서 '기본 방향 → 데이터 방향' 최소 회전(swing)
//   손목: 손바닥 방향까지 맞추기 위해 (손목→중지 뿌리, 검지 뿌리→소지 뿌리) 두 축으로 전체 회전 계산
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import {
  VRMLoaderPlugin, VRMUtils, VRMSpringBoneCollider,
  VRMSpringBoneColliderShapeCapsule, VRMSpringBoneColliderShapeSphere,
} from "@pixiv/three-vrm";

const FINGERS = [
  // [VRM 뼈 이름(관절 순서), 데이터 점 번호(뿌리→끝)]
  [["ThumbMetacarpal", "ThumbProximal", "ThumbDistal"], [1, 2, 3, 4]],
  [["IndexProximal", "IndexIntermediate", "IndexDistal"], [5, 6, 7, 8]],
  [["MiddleProximal", "MiddleIntermediate", "MiddleDistal"], [9, 10, 11, 12]],
  [["RingProximal", "RingIntermediate", "RingDistal"], [13, 14, 15, 16]],
  [["LittleProximal", "LittleIntermediate", "LittleDistal"], [17, 18, 19, 20]],
];
const SIDES = [
  { pre: "right", sho: 2, elb: 3, wri: 4, hand: "rh" },   // 수어자 오른손 = 아바타 오른손
  { pre: "left", sho: 5, elb: 6, wri: 7, hand: "lh" },
];

// 데이터 좌표 → three.js 월드 방향 (y 위, z 카메라 쪽)
const V = (p) => new THREE.Vector3(p[0], -p[1], -(p[2] || 0));

// 비수지 표현(NMS): 단어마다 붙는 표정·고개 움직임. 수어에서 표정은 문법·의미의 일부.
//   face : 표정 세기 0~1 (angry=눈 찡그림·긴장, sad=걱정, surprised=눈 크게, relaxed=부드러운 얼굴)
//   head : nod(끄덕임) · shake(도리도리) · tilt(갸웃) · up(위를 봄), n = 반복 횟수, deg = 각도
//   데이터(수어자 얼굴)의 입 모양·눈 깜빡임은 그대로 쓰고, 이 표는 단어 뜻에 맞는 표정을 더함
export const NMS = {
  "위험1":    { face: { angry: 0.75, sad: 0.2 }, head: { type: "nod", n: 1, deg: 5 } },          // 경고: 긴장한 얼굴
  "조심1":    { face: { angry: 0.4, surprised: 0.2 }, head: { type: "nod", n: 1, deg: 7 } },     // 주의: 진지하게 한 번 끄덕
  "멈추다1":  { face: { angry: 0.65 }, head: { type: "nod", n: 1, deg: 10 } },                   // 단호하게
  "낙하물0#": { face: { surprised: 0.6 }, head: { type: "up", deg: 10 } },                        // 위를 보며 놀람
  "감전0":    { face: { surprised: 0.75, angry: 0.2 }, head: { type: "back", deg: 6 } },          // 움찔
  "없다1":    { face: { sad: 0.3 }, head: { type: "shake", n: 2, deg: 14 } },                     // 부정: 고개 젓기
  "확인1":    { face: { surprised: 0.25 }, head: { type: "nod", n: 2, deg: 8 } },                 // 확인: 끄덕끄덕
  "이상하다1": { face: { sad: 0.45, angry: 0.15 }, head: { type: "tilt", deg: 10 } },             // 의아: 갸웃
};
const FACE_KEYS = ["aa", "ou", "ee", "blink", "surprised", "angry", "sad", "relaxed"];

export class SignAvatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(0.8, 1.6, 2.2);
    this.scene.add(key);
    this.vrm = null;
    this.driven = new Set();
    this.smooth = 0.55;
    this.baseSmile = { relaxed: 0.0 };   // 평소 표정: 입 다문 기본 얼굴 (웃는 표정은 이 모델에서 입이 벌어짐)
    this.margin = 0.035;         // 손목이 몸 표면보다 최소 이만큼(m) 앞에 오도록          // 프레임 간 흔들림 줄이기 (0=그대로, 1=안 움직임)
    this.last = performance.now();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas);
  }

  async load(url) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const t0 = performance.now();
    const gltf = await loader.loadAsync(url);
    this._loadMs = Math.round(performance.now() - t0);
    const vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);                       // VRM0 모델도 정면(+z)을 보게
    vrm.scene.traverse((o) => { o.frustumCulled = false; });
    this.scene.add(vrm.scene);
    this.vrm = vrm;
    // 단계별 소요 시간(ms) 기록: window.avatar.timings
    this.timings = {};
    const step = (name, fn) => { const t = performance.now(); fn(); this.timings[name] = Math.round(performance.now() - t); };
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
    step("measureBodyFront", () => this._measureBodyFront(this._hipsY, this.neckY));
    step("addHandColliders", () => this._addHandColliders());
    step("styleMaterials", () => this._styleMaterials());
    this._frameCamera();
    this.setFrame(null);
    this._loop();
    return this;
  }

  bone(name) { return this.vrm.humanoid.getNormalizedBoneNode(name); }

  // T자세에서 각 뼈의 기본 방향(월드) 기록
  _captureRest() {
    const h = this.vrm.humanoid;
    h.resetNormalizedPose();
    this.vrm.scene.updateMatrixWorld(true);
    const pos = (n) => { const b = this.bone(n); return b ? b.getWorldPosition(new THREE.Vector3()) : null; };
    // 방향은 모델 자신의 좌표로 기록 (VRM0 모델은 rotateVRM0 로 180° 돌아가 있어서, 월드 방향을 쓰면 회전이 두 번 들어가 팔이 위로 감)
    const inv = this.vrm.scene.getWorldQuaternion(new THREE.Quaternion()).invert();
    const dir = (from, to) => to.sub(from).normalize().applyQuaternion(inv);
    this.rest = {};
    for (const s of SIDES) {
      const P = s.pre;
      this.rest[P] = {
        upper: dir(pos(P + "UpperArm"), pos(P + "LowerArm")),
        lower: dir(pos(P + "LowerArm"), pos(P + "Hand")),
        handF: dir(pos(P + "Hand"), pos(P + "MiddleProximal")),
        handS: dir(pos(P + "IndexProximal"), pos(P + "LittleProximal")),
        fingers: FINGERS.map(([names]) => names.map((n, i) => {
          const a = pos(P + n), b = i < 2 ? pos(P + names[i + 1]) : null;
          return b ? dir(a, b) : null;                     // 끝마디는 바로 앞 마디 방향 사용
        })),
      };
      const fr = this.rest[P].fingers;
      fr.forEach((f) => { f[2] = f[1].clone(); });
    }
    for (const s of SIDES) {
      const P = s.pre, R = this.rest[P];
      R.sho = pos(P + "UpperArm");
      R.a = pos(P + "LowerArm").distanceTo(R.sho);
      R.b = pos(P + "Hand").distanceTo(pos(P + "LowerArm"));
    }
    this.headY = pos("head").y;
    this.neckY = pos("neck").y;
    this.neckPos = pos("neck");
    this._hipsY = pos("hips").y;
    this.shoulderW = pos("leftUpperArm").distanceTo(pos("rightUpperArm"));
    // 얼굴: 코 높이(목 위로 어깨너비 몇 배)와 머리 중심·반지름 → 얼굴 근처 수어 위치 맞추기, 손이 얼굴을 뚫지 않게
    const eye = pos("leftEye") || pos("head").add(new THREE.Vector3(0, 0.05, 0));
    this.noseAbove = (eye.y - 0.025 - this.neckY) / Math.max(this.shoulderW, 1e-6);
    this.headCenter = new THREE.Vector3(pos("head").x, eye.y - 0.01, pos("head").z);
    this.headR = this.shoulderW * 0.36;
    this._measureBodyFront(pos("hips").y, pos("neck").y);
  }

  // 상체를 살짝 슬림하게: 가슴 뼈의 폭·두께를 줄이고, 그 아래 달린 목·어깨는 원래 크기로 되돌림
  // 장갑 벗기기: 손이 어두우면(장갑) 손·손가락 부분 텍스처를 피부색으로 칠함 → 수어 손 모양이 잘 보이게
  // 장갑은 모델마다 몸(SKIN)이나 옷(CLOTH) 텍스처에 있음 → 손 뼈를 따라 움직이는 삼각형만 골라 칠함
  _bareHands() {
    const h = this.vrm.humanoid;
    const handBones = new Set();
    for (const P of ["left", "right"]) {
      handBones.add(h.getRawBoneNode(P + "Hand"));
      for (const [names] of FINGERS) for (const n of names) handBones.add(h.getRawBoneNode(P + n));
    }
    handBones.delete(null); handBones.delete(undefined);
    const neckBones = new Set([h.getRawBoneNode("neck"), h.getRawBoneNode("head")]);
    const lum = (d) => (0.299 * d[0] + 0.587 * d[1] + 0.114 * d[2]) / 255;
    const canvases = new Map();          // 텍스처 이미지 → 2D 캔버스 (같은 텍스처는 한 번만)
    const canvasOf = (img) => {
      if (!canvases.has(img)) {
        const cv = document.createElement("canvas"); cv.width = img.width; cv.height = img.height;
        const ctx = cv.getContext("2d", { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
        canvases.set(img, { cv, ctx, dirty: false });
      }
      return canvases.get(img);
    };
    const parts = [];
    this.vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh || !o.geometry.index || !o.geometry.attributes.uv || !o.geometry.attributes.skinIndex) return;
      const mat = [].concat(o.material).find((m) => !/Outline/i.test(m.name) && m.map && m.map.image);
      if (mat) parts.push({ o, mat });
    });
    const domFrac = (o, v, set) => {
      const si = o.geometry.attributes.skinIndex, sw = o.geometry.attributes.skinWeight, bones = o.skeleton.bones;
      let w = 0;
      for (let k = 0; k < 4; k++) if (set.has(bones[si.getComponent(v, k)])) w += sw.getComponent(v, k);
      return w;
    };
    // 쉰 자세에서 아래팔 위 위치 (0 팔꿈치 ~ 1 손목). 팔에서 먼 점은 0
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    const wpos = (n) => h.getRawBoneNode(n).getWorldPosition(new THREE.Vector3());
    const arms = ["left", "right"].map((P) => ({ e: wpos(P + "LowerArm"), w: wpos(P + "Hand") }));
    const wristT = (o, k) => {
      const p = o.getVertexPosition(k, new THREE.Vector3()).applyMatrix4(o.matrixWorld);
      const A = p.distanceTo(arms[0].w) < p.distanceTo(arms[1].w) ? arms[0] : arms[1];
      const ax = A.w.clone().sub(A.e), t = p.clone().sub(A.e).dot(ax) / ax.lengthSq();
      const radial = p.clone().sub(A.e.clone().addScaledVector(ax, t)).length();
      return radial < ax.length() * 0.5 ? t : 0;
    };
    const uvPx = (o, v, img) => [o.geometry.attributes.uv.getX(v) * img.width, o.geometry.attributes.uv.getY(v) * img.height];
    const sample = (o, v, mat) => {
      const img = mat.map.image, { ctx } = canvasOf(img), [x, y] = uvPx(o, v, img);
      return ctx.getImageData(Math.max(0, Math.min(img.width - 1, x | 0)), Math.max(0, Math.min(img.height - 1, y | 0)), 1, 1).data;
    };
    // 피부색: 피부(SKIN) 재질에서 목·머리 뼈를 따르는 점들의 가운데 밝기
    const skin = [];
    for (const { o, mat } of parts) {
      if (!/SKIN/i.test(mat.name)) continue;
      const n = o.geometry.attributes.uv.count;
      for (let v = 0; v < n && skin.length < 60; v += 5) if (domFrac(o, v, neckBones) > 0.9) skin.push(sample(o, v, mat));
    }
    if (!skin.length) return;
    skin.sort((p, q) => lum(q) - lum(p));
    const c0 = skin[Math.floor(skin.length / 2)], fill = `rgb(${c0[0]},${c0[1]},${c0[2]})`;
    const jobs = [];
    for (const { o, mat } of parts) {
      const idx = o.geometry.index, tris = [];
      for (let t = 0; t < idx.count; t += 3) {
        const a = idx.getX(t), b = idx.getX(t + 1), c = idx.getX(t + 2);
        // 피부(SKIN)는 손목 경계까지(손 뼈 비중 15%↑), 옷은 손 부분만(60%↑). 어두운 곳만 칠하므로 맨살은 그대로
        const th = /SKIN/i.test(mat.name) ? 0.15 : 0.6;
        const nearWrist = (k) => /SKIN/i.test(mat.name) && wristT(o, k) > 0.8;
        const ok = (k) => domFrac(o, k, handBones) > th || nearWrist(k);
        if (/SKIN/i.test(mat.name) ? [a, b, c].some(ok) : [a, b, c].every(ok)) tris.push([a, b, c]);   // 피부는 경계에 걸친 삼각형까지
      }
      if (!tris.length) continue;
      const img = mat.map.image, C = canvasOf(img);
      // 어두운 삼각형(장갑 부분)만: 세 꼭짓점과 가운데 색의 밝기로 판단 (손가락 없는 장갑도 처리)
      const dark = tris.filter(([a, b, c]) => {
        const [ax, ay] = uvPx(o, a, img), [bx, by] = uvPx(o, b, img), [cx, cy] = uvPx(o, c, img);
        const mid = C.ctx.getImageData(Math.min(img.width - 1, ((ax + bx + cx) / 3) | 0), Math.min(img.height - 1, ((ay + by + cy) / 3) | 0), 1, 1).data;
        return lum(mid) < 0.4;
      });
      jobs.push({ o, mat, tris, dark, img, C });
    }
    const gloved = jobs.some((j) => j.dark.length);               // 모델 어딘가에 장갑이 있으면
    for (const { o, mat, tris, dark, img, C } of jobs) {
      if (!gloved || (!dark.length && !/SKIN/i.test(mat.name))) continue;   // 맨손 모델은 그대로, 피부는 모든 부분 정리
      C.ctx.fillStyle = fill; C.ctx.strokeStyle = fill; C.ctx.lineWidth = 2;
      for (const [a, b, c] of dark) {
        const [ax, ay] = uvPx(o, a, img), [bx, by] = uvPx(o, b, img), [cx, cy] = uvPx(o, c, img);
        C.ctx.beginPath(); C.ctx.moveTo(ax, ay); C.ctx.lineTo(bx, by); C.ctx.lineTo(cx, cy); C.ctx.closePath(); C.ctx.fill(); C.ctx.stroke();
      }
      // 밝은 삼각형 안에 남은 가는 장갑 줄무늬: 손 부분 픽셀 중 피부보다 확 어두운 픽셀만 피부색으로
      if (/SKIN/i.test(mat.name)) {
        const W = img.width, H = img.height, data = C.ctx.getImageData(0, 0, W, H), d = data.data;
        const skinL = lum(c0);
        for (const [a, b, c] of tris) {
          const U = [a, b, c].map((k) => uvPx(o, k, img));
          const x0 = Math.max(0, Math.floor(Math.min(U[0][0], U[1][0], U[2][0])) - 1), x1 = Math.min(W - 1, Math.ceil(Math.max(U[0][0], U[1][0], U[2][0])) + 1);
          const y0 = Math.max(0, Math.floor(Math.min(U[0][1], U[1][1], U[2][1])) - 1), y1 = Math.min(H - 1, Math.ceil(Math.max(U[0][1], U[1][1], U[2][1])) + 1);
          const den = (U[1][1] - U[2][1]) * (U[0][0] - U[2][0]) + (U[2][0] - U[1][0]) * (U[0][1] - U[2][1]);
          if (Math.abs(den) < 1e-9) continue;
          for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
            const px = x + 0.5, py = y + 0.5;
            const w0 = ((U[1][1] - U[2][1]) * (px - U[2][0]) + (U[2][0] - U[1][0]) * (py - U[2][1])) / den;
            const w1 = ((U[2][1] - U[0][1]) * (px - U[2][0]) + (U[0][0] - U[2][0]) * (py - U[2][1])) / den;
            if (w0 < -0.05 || w1 < -0.05 || 1 - w0 - w1 < -0.05) continue;
            const k = (y * W + x) * 4;
            if (lum([d[k], d[k + 1], d[k + 2]]) < skinL * 0.7) { d[k] = c0[0]; d[k + 1] = c0[1]; d[k + 2] = c0[2]; }
          }
        }
        C.ctx.putImageData(data, 0, 0);
      }
      C.dirty = true;
    }
    // 칠한 텍스처를 그 텍스처를 쓰는 모든 재질에 반영
    for (const [img, C] of canvases) {
      if (!C.dirty) continue;
      this.vrm.scene.traverse((o) => {
        if (!o.isMesh) return;
        for (const m of [].concat(o.material)) {
          for (const key of ["map", "shadeMultiplyTexture"]) {
            const t = m[key];
            if (t && t.image === img) {
              const nt = new THREE.CanvasTexture(C.cv);
              nt.flipY = t.flipY; nt.colorSpace = t.colorSpace; nt.wrapS = t.wrapS; nt.wrapT = t.wrapT;
              m[key] = nt; m.needsUpdate = true;
            }
          }
        }
      });
    }
  }

  // 옷에 늘 가려지는 몸(몸통·위팔) 피부는 아예 안 그림 → 옷을 몸에 바짝 붙여도 피부가 옷을 뚫고 나오지 않음
  _hideCoveredBody() {
    const h = this.vrm.humanoid;
    const covered = new Set(["hips", "spine", "chest", "upperChest", "leftShoulder", "rightShoulder", "leftUpperArm", "rightUpperArm"]
      .map((n) => h.getRawBoneNode(n)).filter(Boolean));
    this.vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh || !/Body.*SKIN/i.test([].concat(o.material).map((m) => m.name).join(" "))) return;
      const g = o.geometry, idx = g.index, si = g.attributes.skinIndex, sw = g.attributes.skinWeight, bones = o.skeleton.bones;
      if (!idx) return;
      const main = (k) => { let best = 0, bi = 0; for (let j = 0; j < 4; j++) if (sw.getComponent(k, j) > best) { best = sw.getComponent(k, j); bi = si.getComponent(k, j); } return bones[bi]; };
      const keep = [];
      // 그룹(재질별 범위)을 유지하며 가려진 삼각형만 뺌
      const groups = g.groups.length ? g.groups : [{ start: 0, count: idx.count, materialIndex: 0 }];
      const ng = [];
      for (const gr of groups) {
        const start = keep.length;
        for (let t = gr.start; t < gr.start + gr.count; t += 3) {
          const tri = [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)];
          if (tri.every((k) => covered.has(main(k)))) continue;
          keep.push(...tri);
        }
        ng.push({ start, count: keep.length - start, materialIndex: gr.materialIndex });
      }
      g.setIndex(keep);
      g.clearGroups(); if (g.groups !== undefined && groups.length > 1) ng.forEach((x) => g.addGroup(x.start, x.count, x.materialIndex));
      else ng.forEach((x) => g.addGroup(x.start, x.count, x.materialIndex));
    });
  }

  // 몸에 붙는 상의: 윗옷 꼭짓점을 가장 가까운 몸(피부) 표면 바로 바깥으로 당기고, 그 몸 부분과 같은 뼈를 따르게 함
  // (몸이 없는 곳 — 옷에 가려 지워진 부분 — 은 가까운 뼈 축 쪽으로 줄임)
  _tightTop(gap = 0.007, keep = 0.12) {
    const V = THREE.Vector3;
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    const body = [];                                   // [pos, normal, skinIndex4, skinWeight4, mesh]
    let tops = null;
    this.vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh) return;
      const n = [].concat(o.material).map((m) => m.name).join(" ");
      if (/Tops/i.test(n)) tops = o;
      if (!/Body.*SKIN/i.test(n)) return;
      const g = o.geometry, pos = g.attributes.position, nor = g.attributes.normal, si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
      const nm = new THREE.Matrix3().getNormalMatrix(o.matrixWorld);
      for (let k = 0; k < pos.count; k++) {
        body.push({ p: o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld),
                    n: new V().fromBufferAttribute(nor, k).applyMatrix3(nm).normalize(),
                    bones: [0, 1, 2, 3].map((j) => o.skeleton.bones[si.getComponent(k, j)]),
                    w: [0, 1, 2, 3].map((j) => sw.getComponent(k, j)) });
      }
    });
    if (!tops || !body.length) return;
    const cell = 0.02, key = (p) => `${Math.floor(p.x / cell)},${Math.floor(p.y / cell)},${Math.floor(p.z / cell)}`;
    const grid = new Map();
    body.forEach((b, i) => { const k = key(b.p); (grid.get(k) || grid.set(k, []).get(k)).push(i); });
    const nearest = (p) => {
      let best = -1, bd = Infinity;
      const cx = Math.floor(p.x / cell), cy = Math.floor(p.y / cell), cz = Math.floor(p.z / cell);
      for (let dx = -3; dx <= 3; dx++) for (let dy = -3; dy <= 3; dy++) for (let dz = -3; dz <= 3; dz++) {
        const l = grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
        if (l) for (const i of l) { const d = body[i].p.distanceToSquared(p); if (d < bd) { bd = d; best = i; } }
      }
      return best < 0 ? null : { b: body[best], d: Math.sqrt(bd) };
    };
    const g = tops.geometry, pos = g.attributes.position, si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
    const bones = tops.skeleton.bones, inv = tops.matrixWorld.clone().invert();
    const hm = this.vrm.humanoid;
    const armBones = new Set(["left", "right"].flatMap((P) => [hm.getRawBoneNode(P + "UpperArm"), hm.getRawBoneNode(P + "LowerArm")]));
    let moved = 0;
    for (let k = 0; k < pos.count; k++) {
      const p = tops.getVertexPosition(k, new V()).applyMatrix4(tops.matrixWorld);
      const hit = nearest(p);
      if (!hit || hit.d > keep) continue;
      const { b } = hit;
      const out = p.clone().sub(b.p).dot(b.n);          // 몸 표면 바깥쪽 거리
      if (out < gap) continue;                          // 이미 붙어 있음
      // 몸에서 떨어진 거리를 줄이되 비율로 줄여서 겹친 옷(안쪽 셔츠·바깥 점퍼)의 순서는 유지
      const onArm = b.bones.some((bn, j) => armBones.has(bn) && b.w[j] > 0.5);   // 팔(소매)은 겹친 옷이 없어서 더 바짝
      const target = b.p.clone().addScaledVector(b.n, gap + (out - gap) * (onArm ? 0.1 : 0.42)).add(p.clone().sub(b.p).projectOnPlane(b.n));
      const local = target.applyMatrix4(inv);
      pos.setXYZ(k, local.x, local.y, local.z);
      // 붙은 몸 부분과 같은 뼈를 따르게 (팔·몸을 움직여도 옷이 피부를 뚫지 않음)
      const idx = b.bones.map((bn) => bones.indexOf(bn));
      if (idx.every((x, j) => x >= 0 || b.w[j] === 0)) {
        si.setXYZW(k, ...idx.map((x) => Math.max(0, x)));
        sw.setXYZW(k, ...b.w);
      }
      moved++;
    }
    pos.needsUpdate = true; si.needsUpdate = true; sw.needsUpdate = true;
    g.computeVertexNormals(); g.computeBoundingSphere();
    this._tightMoved = [moved, pos.count];
  }

  // 소매 줄이기: 손목을 덮는 소매·장갑 부분을 손목 조금 위에서 끝나게 → 손 모양이 다 보임
  // (쉰 자세에서 아래팔 축을 따라 손목 쪽 85% 지점 너머의 옷 꼭짓점을 그 지점으로 당기고, 손 대신 아래팔 뼈를 따르게)
  _shortenSleeves(end = 0.85) {
    const h = this.vrm.humanoid, V = THREE.Vector3;
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    const wp = (n) => h.getRawBoneNode(n).getWorldPosition(new V());
    const arms = ["left", "right"].map((P) => ({ e: wp(P + "LowerArm"), w: wp(P + "Hand"), bone: h.getRawBoneNode(P + "LowerArm") }));
    this.vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh) return;
      if (!/CLOTH/i.test([].concat(o.material).map((m) => m.name).join(" ")) || /Bottoms|Shoes/i.test(o.material.name || [].concat(o.material)[0].name)) return;
      const g = o.geometry, pos = g.attributes.position, si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
      const bones = o.skeleton.bones, inv = o.matrixWorld.clone().invert();
      // 1차: 소매 끝 너머 꼭짓점과 아래팔 축 사이 거리(굵기) 수집 → 팔마다 가장 굵은 값 = 소매 굵기
      const info = [], maxR = [0, 0];
      for (let k = 0; k < pos.count; k++) {
        const p = o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld);
        const ai = p.distanceTo(arms[0].w) < p.distanceTo(arms[1].w) ? 0 : 1, A = arms[ai];
        const ax = A.w.clone().sub(A.e), t = p.clone().sub(A.e).dot(ax) / ax.lengthSq();
        if (t <= end) continue;
        const radial = p.clone().sub(A.e.clone().addScaledVector(ax, t));
        if (radial.length() > ax.length() * 0.9) continue;          // 아래팔과 먼 꼭짓점(몸통 등)은 제외
        info.push({ k, ai, A, ax, t, radial });
        if (t < 1.0) maxR[ai] = Math.max(maxR[ai], radial.length());
      }
      // 2차: 소매(굵은 부분)는 end 지점 둘레로 당기고, 손목에 딱 붙는 장갑·손 부분은 소매 안쪽 축으로 접어 넣음
      let moved = 0;
      for (const { k, ai, A, ax, t, radial } of info) {
        const tight = t > 1.0 || radial.length() < maxR[ai] * 0.8;
        const target = tight ? A.e.clone().addScaledVector(ax, end - 0.05)
                             : A.e.clone().addScaledVector(ax, end).add(radial);
        const local = target.applyMatrix4(inv);
        pos.setXYZ(k, local.x, local.y, local.z);
        const bi = bones.indexOf(A.bone);
        if (bi >= 0) { si.setXYZW(k, bi, 0, 0, 0); sw.setXYZW(k, 1, 0, 0, 0); }   // 손목을 돌려도 소매는 아래팔을 따름
        moved++;
      }
      if (moved) { pos.needsUpdate = true; si.needsUpdate = true; sw.needsUpdate = true; g.computeBoundingSphere(); }
    });
  }

  // 눈동자 색: 빨간 눈동자 등 튀는 색이면 검은색으로 (명암은 유지)
  _eyeColor() {
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of [].concat(o.material)) {
        if (!/EyeIris/i.test(m.name) || !m.map || !m.map.image || m.map.userData.recolored) continue;
        const img = m.map.image, cv = document.createElement("canvas"); cv.width = img.width; cv.height = img.height;
        const ctx = cv.getContext("2d", { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, cv.width, cv.height), d = data.data;
        let red = 0, n = 0;
        for (let k = 0; k < d.length; k += 4 * 7) if (d[k + 3] > 20) { n++; if (d[k] > d[k + 1] * 1.4 && d[k] > d[k + 2] * 1.3) red++; }
        if (!n || red / n < 0.2) continue;                        // 이미 자연스러운 색이면 그대로
        const dark = [10, 9, 9], light = [58, 50, 46];             // 검은 눈동자
        for (let k = 0; k < d.length; k += 4) {
          if (d[k + 3] <= 8) continue;
          const l = Math.max(d[k], d[k + 1], d[k + 2]) / 255;      // 붉은 눈은 R 이 밝기
          const sat = Math.max(d[k], d[k + 1], d[k + 2]) - Math.min(d[k], d[k + 1], d[k + 2]);
          if (sat < 25 && l > 0.85) continue;                       // 하이라이트(흰 점)는 그대로
          for (let c = 0; c < 3; c++) d[k + c] = dark[c] + (light[c] - dark[c]) * l;
        }
        ctx.putImageData(data, 0, 0);
        const tex = new THREE.CanvasTexture(cv), t0 = m.map;
        tex.flipY = t0.flipY; tex.colorSpace = t0.colorSpace; tex.wrapS = t0.wrapS; tex.wrapT = t0.wrapT;
        tex.userData.recolored = true;
        for (const key of ["map", "shadeMultiplyTexture"]) if (m[key] && m[key].image === img) m[key] = tex;
        m.needsUpdate = true;
      }
    });
  }

  // 작업복: 윗옷 텍스처를 남색 작업복 색으로 다시 칠하고(주름·지퍼 명암은 유지), 가슴·팔에 반사띠
  _workwear() {
    let done = false;
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    const h = this.vrm.humanoid, V = THREE.Vector3;
    const wp = (n) => { const b = h.getRawBoneNode(n); return b ? b.getWorldPosition(new V()) : null; };
    const chestY = (wp("upperChest") || wp("chest")).y, hipsY = wp("hips").y;
    this.vrm.scene.traverse((o) => {
      if (done || !o.isSkinnedMesh) return;
      const mats = [].concat(o.material);
      const mat = mats.find((m) => /Tops/i.test(m.name) && !/Outline/i.test(m.name) && m.map && m.map.image);
      if (!mat) return;
      const img = mat.map.image, W = img.width, H = img.height;
      const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
      const ctx = cv.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0);
      // 1) 색 바꾸기: 밝기(명암)만 남기고 작업복 남색으로
      const data = ctx.getImageData(0, 0, W, H), d = data.data;
      const L = [];
      for (let k = 0; k < d.length; k += 4 * 13) if (d[k + 3] > 8) L.push(0.299 * d[k] + 0.587 * d[k + 1] + 0.114 * d[k + 2]);
      L.sort((a, b) => a - b);
      const lo = L[Math.floor(L.length * 0.02)] || 0, hi = L[Math.floor(L.length * 0.98)] || 255;
      const dark = [36, 48, 68], light = [92, 110, 136];           // 작업복 남색 (그늘 ~ 밝은 면)
      for (let k = 0; k < d.length; k += 4) {
        if (d[k + 3] <= 8) continue;
        const l = 0.299 * d[k] + 0.587 * d[k + 1] + 0.114 * d[k + 2];
        const sat = Math.max(d[k], d[k + 1], d[k + 2]) - Math.min(d[k], d[k + 1], d[k + 2]);
        // 원래 옷의 배색(밝은 판·어두운 판)과 장식은 없애고 주름 명암만 살짝 남김 → 한 가지 색 작업복
        const t0 = Math.min(1, Math.max(0, (l - lo) / Math.max(1, hi - lo)));
        const t = 0.5 + (t0 - 0.5) * 0.22;
        for (let c = 0; c < 3; c++) d[k + c] = dark[c] + (light[c] - dark[c]) * t;
      }
      // 2) 반사띠: 가슴 높이 띠 + 양팔 아래팔 띠. 삼각형을 텍스처 위에 픽셀 단위로 그려가며
      //    그 픽셀의 3D 위치(쉰 자세)가 띠 안이면 칠함 → 띠 경계가 곧게
      const g = o.geometry, idx = g.index, uv = g.attributes.uv;
      const si = g.attributes.skinIndex, sw = g.attributes.skinWeight, bones = o.skeleton.bones;
      const lowerArms = new Set([h.getRawBoneNode("leftLowerArm"), h.getRawBoneNode("rightLowerArm")]);
      const world = (k) => o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld);
      const onBones = (k, set) => { let w = 0; for (let j = 0; j < 4; j++) if (set.has(bones[si.getComponent(k, j)])) w += sw.getComponent(k, j); return w; };
      const bandY = hipsY + (chestY - hipsY) * 0.95, bandH = 0.024;
      const arm = {};
      for (const P of ["left", "right"]) arm[P] = { e: wp(P + "LowerArm"), w: wp(P + "Hand") };
      const armT = (p) => {                                       // 아래팔에서의 위치 0(팔꿈치)~1(손목)
        const A = Math.abs(p.x - arm.left.e.x) < Math.abs(p.x - arm.right.e.x) ? arm.left : arm.right;
        const ax = A.w.clone().sub(A.e); return p.clone().sub(A.e).dot(ax) / ax.lengthSq();
      };
      const tape = [214, 220, 226];
      const inBand = (p, isArm) => isArm ? Math.abs(armT(p) - 0.42) < 0.05 : Math.abs(p.y - bandY) < bandH / 2 && Math.abs(p.x) < 0.22;
      for (let t = 0; t < idx.count; t += 3) {
        const tri = [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)];
        const isArm = tri.every((k) => onBones(k, lowerArms) > 0.5);
        const P = tri.map(world);
        // 빠른 거르기: 삼각형이 띠 근처에 있을 때만
        if (isArm) { const ts = P.map(armT); if (Math.max(...ts) < 0.3 || Math.min(...ts) > 0.55) continue; }
        else { const ys = P.map((q) => q.y); if (Math.max(...ys) < bandY - bandH || Math.min(...ys) > bandY + bandH) continue; }
        const U = tri.map((k) => [uv.getX(k) * W, uv.getY(k) * H]);
        const x0 = Math.max(0, Math.floor(Math.min(U[0][0], U[1][0], U[2][0]))), x1 = Math.min(W - 1, Math.ceil(Math.max(U[0][0], U[1][0], U[2][0])));
        const y0 = Math.max(0, Math.floor(Math.min(U[0][1], U[1][1], U[2][1]))), y1 = Math.min(H - 1, Math.ceil(Math.max(U[0][1], U[1][1], U[2][1])));
        const den = (U[1][1] - U[2][1]) * (U[0][0] - U[2][0]) + (U[2][0] - U[1][0]) * (U[0][1] - U[2][1]);
        if (Math.abs(den) < 1e-9) continue;
        const q = new V();
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
          const px = x + 0.5, py = y + 0.5;
          const w0 = ((U[1][1] - U[2][1]) * (px - U[2][0]) + (U[2][0] - U[1][0]) * (py - U[2][1])) / den;
          const w1 = ((U[2][1] - U[0][1]) * (px - U[2][0]) + (U[0][0] - U[2][0]) * (py - U[2][1])) / den;
          const w2 = 1 - w0 - w1;
          if (w0 < -0.02 || w1 < -0.02 || w2 < -0.02) continue;
          q.set(0, 0, 0).addScaledVector(P[0], w0).addScaledVector(P[1], w1).addScaledVector(P[2], w2);
          if (!inBand(q, isArm)) continue;
          const k = (y * W + x) * 4;
          d[k] = tape[0]; d[k + 1] = tape[1]; d[k + 2] = tape[2];
        }
      }
      ctx.putImageData(data, 0, 0);
      const tex = new THREE.CanvasTexture(cv);
      const t0 = mat.map; tex.flipY = t0.flipY; tex.colorSpace = t0.colorSpace; tex.wrapS = t0.wrapS; tex.wrapT = t0.wrapT;
      for (const m of mats) {
        for (const key of ["map", "shadeMultiplyTexture"]) if (m[key] && m[key].image === img) m[key] = tex;
        if (m.color && !/Outline/i.test(m.name)) m.color.set(0xffffff);
        if (m.shadeColorFactor) m.shadeColorFactor.set(0x9aa6b8);
        m.needsUpdate = true;
      }
      done = true;
    });
    this._workwearDone = done;
  }

  // 빡빡머리: 머리카락을 모두 숨기고, 얼굴(머리) 피부 텍스처의 두피 부분에 짧게 민 머리 자국을 칠함
  _buzzCut(strength = 0.82) {
    const V = THREE.Vector3, hm = this.vrm.humanoid;
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    this.vrm.scene.traverse((o) => { if (o.isMesh && /HAIR/i.test([].concat(o.material).map((m) => m.name).join(" "))) o.visible = false; });
    this.vrm.scene.traverse((o) => { if (o.isMesh && /Bottoms/i.test([].concat(o.material).map((m) => m.name).join(" "))) o.visible = false; });   // 바지(화면 밖)는 숨김: 허리띠가 웃옷 아래로 비치지 않게
    const eye = hm.getRawBoneNode("leftEye"), head = hm.getRawBoneNode("head");
    if (!eye || !head) return;
    let eyeBox = new THREE.Box3();
    this.vrm.scene.traverse((o) => { if (o.isMesh && /EyeWhite/i.test([].concat(o.material).map((m) => m.name).join(" "))) eyeBox.expandByObject(o); });
    const eyeY = eyeBox.isEmpty() ? eye.getWorldPosition(new V()).y + 0.02 : eyeBox.max.y;   // 눈 위쪽 끝 높이
    let face = null;
    this.vrm.scene.traverse((o) => {
      if (o.isMesh && /Face_00_SKIN/i.test([].concat(o.material).map((m) => m.name).join(" "))) {
        o.geometry.computeBoundingBox(); const b = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld); face = face ? face.union(b) : b;
      }
    });
    if (!face) return;
    const front = eyeBox.isEmpty() ? face.max.z - 0.02 : eyeBox.max.z, fcx = (face.min.x + face.max.x) / 2;   // 깊이 기준 = 눈 앞면
    const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
    // 머리 자국 진하기: 이마 위(앞머리선) + 옆·뒤통수(귀 높이 위). 얼굴·귀 앞은 비움
    const amount = (q) => {
      const depth = front - q.z;                               // 얼굴 앞면에서 뒤로 얼마나
      const top = smooth(eyeY + 0.035, eyeY + 0.05, q.y);                                      // 이마 위 머리선
      const side = smooth(0.05, 0.07, depth) * smooth(eyeY + 0.005, eyeY + 0.02, q.y);         // 관자놀이·귀 위
      const back = smooth(0.1, 0.12, depth) * smooth(eyeY - 0.06, eyeY - 0.045, q.y);          // 귀 뒤·뒤통수
      return Math.max(top, side, back);
    };
    const done = new Map();
    this.vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh) return;
      const mats = [].concat(o.material);
      const mat = mats.find((m) => /Face_00_SKIN/i.test(m.name) && !/Outline/i.test(m.name) && m.map && m.map.image);
      if (!mat) return;
      const img = mat.map.image, W = img.width, H = img.height;
      let C = done.get(img);
      if (!C) {
        const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
        const ctx = cv.getContext("2d", { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
        C = { cv, ctx, data: ctx.getImageData(0, 0, W, H), mask: new Float32Array(W * H), mats: [] };
        done.set(img, C);
      }
      C.mats.push(o);
      const g = o.geometry, idx = g.index, uv = g.attributes.uv;
      const P = (k) => o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld);
      for (let t = 0; t < idx.count; t += 3) {
        const tri = [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)], Q = tri.map(P);
        if (Math.max(...Q.map((q) => q.y)) < eyeY - 0.07) continue;
        const U = tri.map((k) => [uv.getX(k) * W, uv.getY(k) * H]);
        const x0 = Math.max(0, Math.floor(Math.min(U[0][0], U[1][0], U[2][0]))), x1 = Math.min(W - 1, Math.ceil(Math.max(U[0][0], U[1][0], U[2][0])));
        const y0 = Math.max(0, Math.floor(Math.min(U[0][1], U[1][1], U[2][1]))), y1 = Math.min(H - 1, Math.ceil(Math.max(U[0][1], U[1][1], U[2][1])));
        const den = (U[1][1] - U[2][1]) * (U[0][0] - U[2][0]) + (U[2][0] - U[1][0]) * (U[0][1] - U[2][1]);
        if (Math.abs(den) < 1e-9) continue;
        const q = new V();
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
          const w0 = ((U[1][1] - U[2][1]) * (x + 0.5 - U[2][0]) + (U[2][0] - U[1][0]) * (y + 0.5 - U[2][1])) / den;
          const w1 = ((U[2][1] - U[0][1]) * (x + 0.5 - U[2][0]) + (U[0][0] - U[2][0]) * (y + 0.5 - U[2][1])) / den;
          if (w0 < -0.02 || w1 < -0.02 || 1 - w0 - w1 < -0.02) continue;
          q.set(0, 0, 0).addScaledVector(Q[0], w0).addScaledVector(Q[1], w1).addScaledVector(Q[2], 1 - w0 - w1);
          const a = amount(q), m = y * W + x;
          if (a > C.mask[m]) C.mask[m] = a;
        }
      }
    });
    const tint = [44, 40, 38];
    for (const [img, C] of done) {
      const d = C.data.data;
      for (let m = 0; m < C.mask.length; m++) {
        if (C.mask[m] <= 0.01) continue;
        const k = m * 4, x = m % C.cv.width, y = (m / C.cv.width) | 0;
        const r = Math.abs(Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1;   // 짧은 머리카락 점점이
        const a = Math.min(1, C.mask[m] * strength * (0.75 + 0.35 * r));
        for (let c = 0; c < 3; c++) d[k + c] = d[k + c] * (1 - a) + tint[c] * a;
      }
      C.ctx.putImageData(C.data, 0, 0);
      const tex = new THREE.CanvasTexture(C.cv);
      this.vrm.scene.traverse((o) => {
        if (!o.isMesh) return;
        for (const m of [].concat(o.material)) for (const key of ["map", "shadeMultiplyTexture"]) {
          const t0 = m[key];
          if (t0 && t0.image === img) { tex.flipY = t0.flipY; tex.colorSpace = t0.colorSpace; tex.wrapS = t0.wrapS; tex.wrapT = t0.wrapT; m[key] = tex; m.needsUpdate = true; }
        }
      });
    }
  }

  // 단정한 머리: 얼굴 앞으로 내려와 눈을 가리는 앞머리, 턱 아래로 길게 늘어진 옆머리 가닥은 숨김 (안전모가 윗머리를 덮음)
  _tidyHair() {
    const h = this.vrm.humanoid, V = THREE.Vector3;
    this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
    const eye = h.getRawBoneNode("leftEye"), head = h.getRawBoneNode("head"), neck = h.getRawBoneNode("neck");
    if (!eye || !head) return;
    const eyeY = eye.getWorldPosition(new V()).y, headP = head.getWorldPosition(new V()), neckY = neck.getWorldPosition(new V()).y;
    const strands = [];
    this.vrm.scene.traverse((o) => {
      if (o.isMesh && /HAIR/i.test([].concat(o.material).map((m) => m.name).join(" ")) && !/HairBack/i.test(o.material.name || "")) strands.push(o);
    });
    if (strands.length < 8) return;                               // 머리가 한 덩어리인 모델은 건드리지 않음
    let hidden = 0;
    for (const o of strands) {
      o.geometry.computeBoundingBox();
      const b = new THREE.Box3().copy(o.geometry.boundingBox).applyMatrix4(o.matrixWorld);
      const front = (b.min.z + b.max.z) / 2 > headP.z;
      const overEyes = front && b.min.y < eyeY + 0.01 && Math.abs((b.min.x + b.max.x) / 2 - headP.x) < 0.06;
      const longSide = b.min.y < neckY - 0.01;
      if (overEyes || longSide) { o.visible = false; hidden++; }
    }
    this._hairHidden = hidden;
    // 남은 가닥 다듬기: 귀 중간 높이 아래로 내려온 부분은 그 높이에서 자르고, 옆으로 삐친 부분은 머리에 붙게
    let face = null;
    this.vrm.scene.traverse((o) => {
      if (o.isMesh && /Face_00_SKIN/i.test([].concat(o.material).map((m) => m.name).join(" "))) {
        o.geometry.computeBoundingBox(); const b = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
        face = face ? face.union(b) : b;
      }
    });
    const cutY = eyeY - 0.012;
    const R = face ? (face.max.x - face.min.x) / 2 + 0.014 : 0.09;          // 머리 반지름 + 머리카락 두께
    const cx = headP.x, cz = face ? (face.min.z + face.max.z) / 2 - 0.01 : headP.z;
    for (const o of strands) {
      if (!o.visible) continue;
      const pos = o.geometry.attributes.position, inv = o.matrixWorld.clone().invert();
      let changed = false;
      for (let k = 0; k < pos.count; k++) {
        const p = o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld);
        let ch = false;
        if (p.y < cutY) { p.y = cutY; ch = true; }
        const dx = p.x - cx, dz = p.z - cz, r = Math.hypot(dx, dz);
        if (p.y < headP.y + 0.12 && r > R) { p.x = cx + dx * R / r; p.z = cz + dz * R / r; ch = true; }
        if (ch) { const l = p.applyMatrix4(inv); pos.setXYZ(k, l.x, l.y, l.z); changed = true; }
      }
      if (changed) { pos.needsUpdate = true; o.geometry.computeVertexNormals(); o.geometry.computeBoundingSphere(); }
    }
  }

  _slimTorso(sx = 0.9, sz = 0.92) {
    // 골반·허리·가슴을 같은 비율로 가늘게 → 가슴에서 아래로 일직선 (모래시계 체형 X)
    // 목·어깨·다리는 원래 굵기로 되돌림 (회전하지 않는 뼈라 정확히 맞음; 다리는 화면 밖)
    const h = this.vrm.humanoid;
    const hips = h.getRawBoneNode("hips");
    if (!hips) return;
    hips.scale.set(sx, 1, sz);
    for (const n of ["neck", "leftShoulder", "rightShoulder", "leftUpperLeg", "rightUpperLeg"]) {
      const b = h.getRawBoneNode(n);
      if (b) b.scale.set(1 / sx, 1, 1 / sz);
    }
  }

  // 작업 복장: 안전모 + 형광 안전조끼 (모델 파일은 그대로 두고 화면에서만 입힘)
  _dressUp() {
    const h = this.vrm.humanoid;
    this.vrm.update(0);
    this.vrm.scene.updateMatrixWorld(true);
    let tops = null, face = new THREE.Box3(), hair = new THREE.Box3();
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      const mats = [].concat(o.material).map((m) => m.name).join(" ");
      if (/Tops/i.test(mats) && o.isSkinnedMesh) tops = o;
      if (/Face_00_SKIN/i.test(mats)) face.expandByObject(o);
      if (/Hair_00_HAIR/i.test(mats)) hair.expandByObject(o);
    });
    const head = h.getRawBoneNode("head");

    // ---- 안전모: 실제 안전모 비율(둥근 셸 + 앞으로 더 나온 챙), 캐릭터 화풍에 맞춘 툰 재질 + 외곽선
    if (head && !face.isEmpty()) {
      const fc = face.getCenter(new THREE.Vector3()), fs = face.getSize(new THREE.Vector3());
      const top = hair.isEmpty() ? face.max.y + 0.03 : Math.min(hair.max.y, face.max.y + 0.06);
      // 눈썹 높이: 눈 뼈 + 약간 (없으면 얼굴 상단에서 추정)
      const eye = this.vrm.humanoid.getRawBoneNode("leftEye");
      // 머리통 크기 재기: 이마 높이에서 좌우·앞뒤로, 정수리는 위에서 광선을 쏴 얼굴(머리) 피부에 닿는 곳
      const faceMeshes = [];
      this.vrm.scene.traverse((o) => { if (o.isMesh && /SKIN/i.test([].concat(o.material).map((m) => m.name).join(" "))) faceMeshes.push(o); });   // 머리 피부(얼굴·몸 메시 모두)
      let eyeBox = new THREE.Box3();
      this.vrm.scene.traverse((o) => { if (o.isMesh && /EyeWhite/i.test([].concat(o.material).map((m) => m.name).join(" "))) eyeBox.expandByObject(o); });
      const eyeTop = eyeBox.isEmpty() ? (eye ? eye.getWorldPosition(new THREE.Vector3()).y + 0.02 : face.max.y - fs.y * 0.35) : eyeBox.max.y;
      const brow = eyeTop + 0.012;                                 // 눈썹 바로 위
      const rc = new THREE.Raycaster();
      const hit = (from, dir) => { rc.set(from, dir.normalize()); const r = rc.intersectObjects(faceMeshes, false)[0]; return r ? r.point : null; };
      const yM = brow + 0.03, far = 1;
      const Fr = hit(new THREE.Vector3(fc.x, yM, fc.z + far), new THREE.Vector3(0, 0, -1)), Bk = hit(new THREE.Vector3(fc.x, yM, fc.z - far), new THREE.Vector3(0, 0, 1));
      const czM = Fr && Bk ? (Fr.z + Bk.z) / 2 : fc.z;            // 머리 앞뒤 가운데에서 폭을 잼 (가장 넓은 곳)
      const L = hit(new THREE.Vector3(fc.x - far, yM, czM), new THREE.Vector3(1, 0, 0)), Rt = hit(new THREE.Vector3(fc.x + far, yM, czM), new THREE.Vector3(-1, 0, 0));
      const Tp = hit(new THREE.Vector3(fc.x, fc.y + far, czM), new THREE.Vector3(0, -1, 0));
      const skull = L && Rt && Fr && Bk ? { cx: (L.x + Rt.x) / 2, cz: (Fr.z + Bk.z) / 2, rx: (Rt.x - L.x) / 2, rz: (Fr.z - Bk.z) / 2 } : null;
      const R = skull ? skull.rx + 0.02 : Math.max(fs.x * 0.56, 0.08);    // 셸 반지름 = 머리통 반폭 + 2cm (실제 안전모처럼 머리를 감쌀 여유)
      const zs = skull ? (skull.rz + 0.02) / R : 1.15;                     // 앞뒤 길이 비율
      const topY = Tp ? Tp.y : top;
      const Hh = Math.max(R * 0.7, topY - brow + 0.018);                    // 셸 높이 = 정수리 + 여유 (낮고 둥글게)
      const grad = new THREE.DataTexture(new Uint8Array([90, 170, 255]), 3, 1, THREE.RedFormat);
      grad.minFilter = grad.magFilter = THREE.NearestFilter; grad.needsUpdate = true;
      const toon = new THREE.MeshToonMaterial({ color: 0xf5b800, gradientMap: grad });
      const toon2 = toon.clone(); toon2.side = THREE.DoubleSide;
      const ink = new THREE.MeshBasicMaterial({ color: 0x3a2a12, side: THREE.BackSide });
      const shellPts = [];
      for (let i = 0; i <= 24; i++) {                            // 아래 테두리 → 꼭대기
        const t = (i / 24) * Math.PI / 2;
        shellPts.push(new THREE.Vector2(Math.max(R * Math.cos(t), 0.0005), Hh * Math.pow(Math.sin(t), 0.8)));
      }
      const hat = new THREE.Group();
      const shell = new THREE.Mesh(new THREE.LatheGeometry(shellPts, 64), toon);
      const shellInk = new THREE.Mesh(shell.geometry, ink);
      shell.scale.set(1, 1, zs); shellInk.scale.set(1.03, 1.03, zs * 1.03);
      // 앞챙: 앞쪽 140°만 길게 나온 차양 (뒤·옆은 얇은 테두리만)
      const visor = new THREE.Mesh(new THREE.LatheGeometry([
        new THREE.Vector2(R * 0.98, 0.006), new THREE.Vector2(R * 1.2, -0.008),
        new THREE.Vector2(R * 1.2, -0.013), new THREE.Vector2(R * 0.98, -0.002),
      ], 48, -Math.PI * 0.39, Math.PI * 0.78), toon2);
      visor.scale.set(1, 1, zs);
      const lip = new THREE.Mesh(new THREE.TorusGeometry(R * 1.01, 0.006, 8, 64), toon);
      lip.rotation.x = Math.PI / 2; lip.scale.set(1, zs, 1);
      // 꼭대기 앞뒤로 지나는 보강 능선 3줄 (셸과 같은 색, 살짝 솟음)
      for (const ang of [-0.32, 0, 0.32]) {
        const pts = [];
        for (let i = 0; i <= 20; i++) {
          const t = 0.18 * Math.PI + (i / 20) * 0.64 * Math.PI;   // 앞쪽 일부 ~ 뒤쪽 일부
          const r = R * Math.cos(ang) * 1.0, y = Hh * Math.pow(Math.sin(t), 0.8);
          const rr = Math.sqrt(Math.max(0, 1 - Math.pow(Math.cos(t), 2))) ;
          pts.push(new THREE.Vector3(Math.sin(ang) * R * rr * 0.98, y * (0.985 + 0.02 * Math.cos(ang)) + 0.002, -Math.cos(t) * R * zs * 0.99));
        }
        const rib = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 32, R * 0.035, 6, false), toon);
        hat.add(rib);
      }
      hat.add(shellInk, shell, visor, lip);
      // 이마 위를 덮도록 낮게 씀
      hat.rotation.x = 0.04;                                     // 거의 수평으로 깊게 씀
      // 머리통 중심에 맞춰 쓰고, 앞 테두리가 눈썹 바로 위에 오도록
      hat.position.set(skull ? skull.cx : fc.x, brow + R * zs * Math.sin(0.04) - 0.004, skull ? skull.cz : fc.z);
      this.scene.add(hat);
      hat.updateMatrixWorld(true);
      head.attach(hat);                                          // 머리 뼈를 따라 움직이도록
      this._hat = hat;
    }

  }

  // 머리카락(스프링 본)이 팔·손을 통과하지 않고 밀려나도록 팔뚝·손에 충돌체를 붙임
  _addHandColliders() {
    const sm = this.vrm.springBoneManager;
    if (!sm) return;
    const colliders = [];
    for (const s of SIDES) {
      const P = s.pre, sign = P === "left" ? 1 : -1, R = this.rest[P];
      const lower = this.bone(P + "LowerArm"), hand = this.bone(P + "Hand");
      const c1 = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeCapsule({
        radius: 0.045, offset: new THREE.Vector3(0, 0, 0), tail: new THREE.Vector3(sign * R.b, 0, 0) }));
      const c2 = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeSphere({
        radius: 0.065, offset: new THREE.Vector3(sign * 0.06, 0, 0) }));
      lower.add(c1); hand.add(c2);
      colliders.push(c1, c2);
    }
    const group = { colliders, name: "signHands" };
    for (const j of sm.joints) j.colliderGroups = [...(j.colliderGroups || []), group];
  }

  // 외곽선 진하게 + 옷을 짙은 작업복 색으로 → 흰 옷 앞의 손이 묻히지 않게
  _styleMaterials() {
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of [].concat(o.material)) {
        if ("outlineWidthFactor" in m && m.outlineWidthFactor > 0) {
          m.outlineWidthFactor *= /SKIN/i.test(m.name) ? 1.8 : 1.3;
          if (m.outlineColorFactor) m.outlineColorFactor.set(0x2a2220);
        }
        if (/Tops/i.test(m.name) && m.color && !this._workwearDone) {
          m.color.set(0x2a2c30);                                  // (작업복 텍스처를 못 만든 경우) 짙은 회색
          if (m.shadeColorFactor) m.shadeColorFactor.set(0x141517);
        }
        if (/SKIN/i.test(m.name) && m.shadeColorFactor) m.shadeColorFactor.set(0xd9a38a);   // 피부 음영 또렷하게
      }
    });
  }

  // 몸통 앞면이 높이(y)마다 z 어디까지 나와 있는지 측정 (머리카락 제외, T자세라 팔은 안 걸림)
  _measureBodyFront(y0, y1) {
    // 높이마다 몸 앞면이 z 어디까지 나와 있는지: 꼭짓점 위치를 한 번만 계산해서 구간별 최댓값
    // (예전: 높이마다 광선을 쏴서 매번 움직이는 메시 전체를 다시 계산 → 20초 가까이 걸렸음)
    this.vrm.update(0);
    this.vrm.scene.updateMatrixWorld(true);
    const step = 0.02, n = Math.max(1, Math.round((y1 + 0.02 - (y0 - 0.1)) / step) + 1);
    const zs = new Array(n).fill(-Infinity), base = y0 - 0.1;
    const v = new THREE.Vector3();
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh || !o.visible) return;
      const mats = [].concat(o.material || []).map((m) => m.name).join(" ");
      if (/hair/i.test(o.name + " " + mats)) return;
      const pos = o.geometry.attributes.position;
      for (let k = 0; k < pos.count; k++) {
        if (o.isSkinnedMesh) o.getVertexPosition(k, v); else v.fromBufferAttribute(pos, k);
        v.applyMatrix4(o.matrixWorld);
        if (Math.abs(v.x) > 0.13) continue;
        const i = Math.round((v.y - base) / step);
        if (i >= 0 && i < n && v.z > zs[i]) zs[i] = v.z;
      }
    });
    this.front = zs.map((z, i) => [base + i * step, z]);
    this.torsoHalf = this.shoulderW * 1.0;   // 헐렁한 옷까지 고려
  }

  frontZ(y) {
    let best = -Infinity;
    for (const [yy, z] of this.front) if (Math.abs(yy - y) <= 0.05) best = Math.max(best, z);
    return best;
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

  // 화면: 안전모 위 ~ 배(골반 바로 위)까지. 수어 동작 중 손은 골반보다 위에 있음(전 동작 확인: 손 최저 ≈ 골반 + 0.15)
  _frameCamera() {
    const V = THREE.Vector3;
    const hips = this.vrm.humanoid.getRawBoneNode("hips").getWorldPosition(new V()).y;
    const top = this._hat ? new THREE.Box3().setFromObject(this._hat).max.y : this.headY + 0.18;
    const bottom = hips + 0.02;
    const pad = 0.02, span = top - bottom + pad;       // 위 여백 거의 없음: 캔버스 위·아래 끝 = 안전모 끝·배
    const half = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const dist = span / 2 / Math.tan(half);
    const cy = bottom + span / 2;
    this.camera.position.set(0, cy, dist);
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
    const world = swing.multiply(parentWorld);                 // swing * parent
    const local = parentWorld.clone().invert().multiply(world);
    this._set(bone, local);
    return world;
  }

  _set(bone, q) {
    const b = typeof bone === "string" ? this.bone(bone) : bone;
    if (!b) return;
    b.userData.target = q;
    this.driven.add(b);
  }

  // 손바닥 두 축으로 손목 전체 회전
  _basis(f, s) {
    const x = f.clone().normalize();
    const z = new THREE.Vector3().crossVectors(x, s).normalize();
    const y = new THREE.Vector3().crossVectors(z, x).normalize();
    return new THREE.Matrix4().makeBasis(x, y, z);
  }

  // fr: 동작 한 프레임, word: { gloss, t } (지금 표현 중인 단어와 그 단어 안 진행도 0~1)
  setFrame(fr, word = null) {
    if (!this.vrm) return;
    this._face = fr;
    this._word = fr ? word : null;
    const neutral = !fr || !fr.body;
    for (const s of SIDES) {
      const P = s.pre, R = this.rest[P];
      const b = fr && fr.body;
      const I = this.vrm.scene.getWorldQuaternion(new THREE.Quaternion());   // 몸통(팔의 부모) 방향 = 모델 전체 회전 (VRM1 은 없음, VRM0 은 180°)
      let upperW, lowerW;
      if (!neutral && b[s.sho] && b[s.elb] && b[s.wri]) {
        // 손목 '위치'를 어깨 기준으로 옮긴 뒤(팔 길이 비율로 축척) IK 로 팔꿈치 계산
        // → 아바타와 수어자의 체형(팔 길이·몸 두께)이 달라도 손이 몸 앞, 같은 높이에 옴
        const sh = V(b[s.sho]), el = V(b[s.elb]), wr = V(b[s.wri]);
        // 손목 목표 = 목 위치 + (데이터의 목→손목) × 아바타 어깨너비. 몸 기준으로 옮겨서
        // 팔이 긴 캐릭터 체형이어도 손이 수어자와 같은 곳(턱·코·가슴 등)에 옴 (팔 길이 비율로 늘리면 손이 아래·앞으로 감)
        const off = wr.clone();
        // 목보다 위(얼굴 높이)는 코 높이 비율로 늘림: 캐릭터 머리가 커도 코·턱 같은 같은 곳을 짚게
        const dn = b[0] ? -b[0][1] : 0;
        if (off.y > 0 && dn > 0.1) off.y *= Math.min(2.2, Math.max(0.8, this.noseAbove / dn));
        const W = this.neckPos.clone().add(off.multiplyScalar(this.shoulderW));
        // 얼굴 안으로 들어가면 얼굴 앞쪽으로 밀어냄
        const fromHead = W.clone().sub(this.headCenter);
        if (fromHead.length() < this.headR) {
          fromHead.z = Math.max(fromHead.z, 0.02);
          W.copy(this.headCenter).add(fromHead.normalize().multiplyScalar(this.headR));
        }
        if (Math.abs(W.x) < this.torsoHalf) {
          const fz = this.frontZ(W.y);
          if (isFinite(fz) && W.z < fz + this.margin) W.z = fz + this.margin;   // 몸 안으로 파묻히지 않게
        }
        const [E, W2] = this._ik(R.sho, W, R.a, R.b, el.clone().sub(sh));
        upperW = this._aim(P + "UpperArm", R.upper, E.clone().sub(R.sho), I);
        lowerW = this._aim(P + "LowerArm", R.lower, W2.clone().sub(E), upperW);
      } else {
        // 팔 내림 (기본 자세)
        const down = new THREE.Vector3(P === "right" ? -0.18 : 0.18, -1, 0.05);
        upperW = this._aim(P + "UpperArm", R.upper, down, I);
        lowerW = this._aim(P + "LowerArm", R.lower, new THREE.Vector3(P === "right" ? -0.05 : 0.05, -1, 0.25), upperW);
      }
      const h = fr && fr[s.hand];
      if (h && h[0] && h[9] && h[5] && h[17]) {
        const f = V(h[9]).sub(V(h[0])), sd = V(h[17]).sub(V(h[5]));
        const M = this._basis(f, sd), M0 = this._basis(R.handF, R.handS);
        const handW = new THREE.Quaternion().setFromRotationMatrix(M.multiply(M0.invert()));
        this._set(P + "Hand", lowerW.clone().invert().multiply(handW));
        FINGERS.forEach(([names, idx], fi) => {
          let parentW = handW;
          names.forEach((n, j) => {
            const a = h[idx[j]], c = h[idx[j + 1]];
            if (a && c) parentW = this._aim(P + n, R.fingers[fi][j], V(c).sub(V(a)), parentW);
          });
        });
      } else {
        // 손 인식 안 됨: 팔 방향 그대로, 손가락 살짝 굽힘
        this._set(P + "Hand", new THREE.Quaternion());
        FINGERS.forEach(([names]) => names.forEach((n) => this._set(P + n, new THREE.Quaternion())));
      }
    }
  }

  // 표정 이름이 모델마다 다름 (이 모델은 '놀람'이 Surprised)
  _expr(name) {
    const em = this.vrm.expressionManager;
    if (!this._exprName) this._exprName = {};
    if (!(name in this._exprName)) {
      const all = Object.keys(em.expressionMap || {});
      this._exprName[name] = all.find((k) => k.toLowerCase() === name.toLowerCase()) || null;
    }
    return this._exprName[name];
  }

  // 비수지 표현: 수어자 얼굴 데이터(입 모양·눈 깜빡임) + 단어별 표정·고개 움직임(NMS) + 평소 눈 깜빡임
  _applyFace(fr) {
    const em = this.vrm.expressionManager;
    const e = (fr && fr.expr) || {};
    const w = this._word, nm = w && NMS[w.gloss];
    const t = w ? Math.max(0, Math.min(1, w.t)) : 0;
    const env = nm ? Math.min(1, t / 0.2, (1 - t) / 0.15 + 0.3) : 0;     // 단어 시작에 표정이 올라오고 끝에 조금 풀림
    const now = performance.now();
    if (em) {
      // 평소 눈 깜빡임 (3~5초마다 0.15초)
      if (!this._nextBlink || now > this._nextBlink + 150) this._nextBlink = now + 3000 + Math.random() * 2000;
      const idleBlink = !fr && now > this._nextBlink ? 1 : 0;
      for (const name of FACE_KEYS) {
        const key = this._expr(name);
        if (!key) continue;
        let tgt = e[name] || 0;
        if (name === "surprised" || name === "angry") tgt *= 0.4;          // 데이터의 눈썹 값은 흔들림이 커서 약하게
        if (nm && nm.face[name]) tgt = Math.max(tgt, nm.face[name] * env);
        if (name === "blink") {
          tgt = Math.max(tgt, idleBlink);
          if (nm && (nm.face.surprised || 0) > 0.4) tgt *= 0.3;             // 놀란 표정 중엔 눈 감지 않게
          else if (nm && nm.face.angry) tgt *= 0.5;                          // 찡그린 눈 + 깜빡임이 윙크처럼 보이지 않게
        }
        const cur = em.getValue(key) || 0;
        em.setValue(key, cur + (tgt - cur) * (name === "blink" ? 0.6 : 0.3));   // 부드럽게
      }
      const other = Math.max(...["surprised", "angry", "sad"].map((n) => (this._expr(n) && em.getValue(this._expr(n))) || 0));
      for (const [name, v] of Object.entries(this.baseSmile)) if (this._expr(name)) em.setValue(this._expr(name), v * (1 - other));
    }
    // 고개: 데이터 방향 + 단어별 움직임 (yaw, pitch(+위), roll)
    const h = ((fr && fr.head) || [0, 0, 0]).map((v) => Math.max(-15, Math.min(15, v)));   // 데이터 고개 각도는 ±15° 까지만 (튀는 값 방지)
    if (nm && nm.head) {
      const H = nm.head, wave = Math.sin(Math.PI * 2 * (H.n || 1) * t), bump = Math.sin(Math.PI * t);
      if (H.type === "nod") h[1] += H.deg * Math.max(0, wave);               // 아래로 끄덕 (이 모델은 + 가 아래)
      else if (H.type === "shake") { h[0] = h[0] * 0.3 + H.deg * wave * env; }
      else if (H.type === "tilt") h[2] += H.deg * env;
      else if (H.type === "up") h[1] -= H.deg * env;
      else if (H.type === "back") h[1] -= H.deg * bump;
    }
    const d = THREE.MathUtils.degToRad;
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(d(-h[1]) * 0.8, d(h[0]) * 0.8, d(h[2]) * 0.8, "YXZ"));
    const half = new THREE.Quaternion().slerp(q, 0.5);
    this._set("neck", half);
    this._set("head", half.clone());
  }

  _loop() {
    const tick = () => {
      requestAnimationFrame(tick);
      const now = performance.now(), dt = Math.min(0.1, (now - this.last) / 1000);
      this.last = now;
      const k = 1 - this.smooth;
      this._applyFace(this._face);
      this.vrm.scene.updateMatrixWorld(true);        // 손 충돌체 위치를 머리카락 계산 전에 최신으로
      for (const o of this.driven) o.quaternion.slerp(o.userData.target, k);
      this.vrm.update(dt);
      this.renderer.render(this.scene, this.camera);
    };
    tick();
  }
}
