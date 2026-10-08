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
    this.margin = 0.07;          // 손목이 몸 표면보다 최소 이만큼(m) 앞에 오도록          // 프레임 간 흔들림 줄이기 (0=그대로, 1=안 움직임)
    this.last = performance.now();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas);
  }

  async load(url) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await loader.loadAsync(url);
    const vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);                       // VRM0 모델도 정면(+z)을 보게
    vrm.scene.traverse((o) => { o.frustumCulled = false; });
    this.scene.add(vrm.scene);
    this.vrm = vrm;
    this._slimTorso();
    this._captureRest();
    this._dressUp();
    this._addHandColliders();
    this._styleMaterials();
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
    this.rest = {};
    for (const s of SIDES) {
      const P = s.pre;
      this.rest[P] = {
        upper: pos(P + "LowerArm").sub(pos(P + "UpperArm")).normalize(),
        lower: pos(P + "Hand").sub(pos(P + "LowerArm")).normalize(),
        handF: pos(P + "MiddleProximal").sub(pos(P + "Hand")).normalize(),
        handS: pos(P + "LittleProximal").sub(pos(P + "IndexProximal")).normalize(),
        fingers: FINGERS.map(([names]) => names.map((n, i) => {
          const a = pos(P + n), b = i < 2 ? pos(P + names[i + 1]) : null;
          return b ? b.sub(a).normalize() : null;        // 끝마디는 바로 앞 마디 방향 사용
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
    this.shoulderW = pos("leftUpperArm").distanceTo(pos("rightUpperArm"));
    this._measureBodyFront(pos("hips").y, pos("neck").y);
  }

  // 상체를 살짝 슬림하게: 가슴 뼈의 폭·두께를 줄이고, 그 아래 달린 목·어깨는 원래 크기로 되돌림
  _slimTorso(sx = 0.9, sz = 0.92) {
    const h = this.vrm.humanoid;
    const chest = h.getRawBoneNode("chest");
    if (!chest) return;
    chest.scale.set(sx, 1, sz);
    for (const n of ["neck", "leftShoulder", "rightShoulder"]) {
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
      const brow = eye ? eye.getWorldPosition(new THREE.Vector3()).y + fs.y * 0.17 : face.max.y - fs.y * 0.3;
      const R = Math.max(fs.x * 0.56, 0.08);                    // 셸 반지름 (머리+머리카락 폭)
      const Hh = Math.max(R * 0.8, top - brow + 0.012);                                        // 셸 높이 (공사장 안전모처럼 높게)
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
      shell.scale.set(1, 1, 1.15); shellInk.scale.set(1.03, 1.03, 1.15 * 1.03);
      // 앞챙: 앞쪽 140°만 길게 나온 차양 (뒤·옆은 얇은 테두리만)
      const visor = new THREE.Mesh(new THREE.LatheGeometry([
        new THREE.Vector2(R * 0.98, 0.006), new THREE.Vector2(R * 1.2, -0.008),
        new THREE.Vector2(R * 1.2, -0.013), new THREE.Vector2(R * 0.98, -0.002),
      ], 48, -Math.PI * 0.39, Math.PI * 0.78), toon2);
      visor.scale.set(1, 1, 1.15);
      const lip = new THREE.Mesh(new THREE.TorusGeometry(R * 1.01, 0.006, 8, 64), toon);
      lip.rotation.x = Math.PI / 2; lip.scale.set(1, 1.15, 1);
      // 꼭대기 앞뒤로 지나는 보강 능선 3줄 (셸과 같은 색, 살짝 솟음)
      for (const ang of [-0.32, 0, 0.32]) {
        const pts = [];
        for (let i = 0; i <= 20; i++) {
          const t = 0.18 * Math.PI + (i / 20) * 0.64 * Math.PI;   // 앞쪽 일부 ~ 뒤쪽 일부
          const r = R * Math.cos(ang) * 1.0, y = Hh * Math.pow(Math.sin(t), 0.8);
          const rr = Math.sqrt(Math.max(0, 1 - Math.pow(Math.cos(t), 2))) ;
          pts.push(new THREE.Vector3(Math.sin(ang) * R * rr * 0.98, y * (0.985 + 0.02 * Math.cos(ang)) + 0.002, -Math.cos(t) * R * 1.15 * 0.99));
        }
        const rib = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 32, R * 0.035, 6, false), toon);
        hat.add(rib);
      }
      hat.add(shellInk, shell, visor, lip);
      // 이마 위를 덮도록 낮게 씀
      hat.rotation.x = 0.1;                                      // 앞이 살짝 내려간 각도
      // 앞 테두리가 눈썹 바로 위에 오도록
      hat.position.set(fc.x, brow + R * 1.15 * Math.sin(0.1), fc.z - fs.z * 0.02);
      this.scene.add(hat);
      hat.updateMatrixWorld(true);
      head.attach(hat);                                          // 머리 뼈를 따라 움직이도록
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
        if (/Tops/i.test(m.name) && m.color) {
          m.color.set(0x2a2c30);                                  // 작업복 검정 (주름·외곽선이 보이도록 아주 짙은 회색)
          if (m.shadeColorFactor) m.shadeColorFactor.set(0x141517);
        }
        if (/SKIN/i.test(m.name) && m.shadeColorFactor) m.shadeColorFactor.set(0xd9a38a);   // 피부 음영 또렷하게
      }
    });
  }

  // 몸통 앞면이 높이(y)마다 z 어디까지 나와 있는지 측정 (머리카락 제외, T자세라 팔은 안 걸림)
  _measureBodyFront(y0, y1) {
    this.vrm.update(0);
    this.vrm.scene.updateMatrixWorld(true);
    const meshes = [];
    this.vrm.scene.traverse((o) => {
      const mats = [].concat(o.material || []).map((m) => m.name).join(" ");
      if (o.isMesh && !/hair/i.test(o.name + " " + mats)) meshes.push(o);
    });
    const rc = new THREE.Raycaster();
    this.front = [];
    for (let y = y0 - 0.1; y <= y1 + 0.02; y += 0.02) {
      let z = -Infinity;
      for (const x of [-0.12, -0.06, 0, 0.06, 0.12]) {
        rc.set(new THREE.Vector3(x, y, 2), new THREE.Vector3(0, 0, -1));
        const hit = rc.intersectObjects(meshes, false)[0];
        if (hit) z = Math.max(z, hit.point.z);
      }
      this.front.push([y, z]);
    }
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

  _frameCamera() {
    const y = (this.neckY + this.headY) / 2 - 0.02;
    const dist = this.shoulderW * 7.2;               // 머리 위까지 여유 있게
    this.camera.position.set(0, y - this.shoulderW * 0.35, dist);
    this.camera.lookAt(0, y - this.shoulderW * 0.35, 0);
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

  setFrame(fr) {
    if (!this.vrm) return;
    this._face = fr;
    const neutral = !fr || !fr.body;
    for (const s of SIDES) {
      const P = s.pre, R = this.rest[P];
      const b = fr && fr.body;
      const I = new THREE.Quaternion();
      let upperW, lowerW;
      if (!neutral && b[s.sho] && b[s.elb] && b[s.wri]) {
        // 손목 '위치'를 어깨 기준으로 옮긴 뒤(팔 길이 비율로 축척) IK 로 팔꿈치 계산
        // → 아바타와 수어자의 체형(팔 길이·몸 두께)이 달라도 손이 몸 앞, 같은 높이에 옴
        const sh = V(b[s.sho]), el = V(b[s.elb]), wr = V(b[s.wri]);
        const dataLen = el.distanceTo(sh) + wr.distanceTo(el);
        const k = (R.a + R.b) / Math.max(dataLen, 1e-6) * 0.97;
        const W = R.sho.clone().add(wr.clone().sub(sh).multiplyScalar(k));
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

  // 비수지 표현: 표정(입 벌림·입 모양·눈 감음·눈썹) + 고개 방향
  _applyFace(fr) {
    const em = this.vrm.expressionManager;
    const e = (fr && fr.expr) || {};
    if (em) {
      for (const name of ["aa", "ou", "ee", "blink", "surprised", "angry"]) {
        if (!em.getExpression(name)) continue;
        const cur = em.getValue(name) || 0, tgt = e[name] || 0;
        em.setValue(name, cur + (tgt - cur) * 0.35);            // 부드럽게
      }
    }
    const h = (fr && fr.head) || [0, 0, 0];
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
