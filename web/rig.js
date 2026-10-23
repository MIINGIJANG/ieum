// 일반 GLB 캐릭터(Character Creator 4, Mixamo/MPFB/Ready Player Me, Rocketbox(3ds Max Biped 'Bip01 ...') 이름 규칙 등) → VRM 과 같은 '사람 뼈대'로 묶기.
// three-vrm 의 VRMHumanoid 를 그대로 써서 '정규화 뼈대'(기본 자세에서 모든 회전 0)를 만들면
// 아바타 코드(avatar.js)는 VRM 이든 CC4 든 같은 방식으로 움직일 수 있다.
//
// 뼈 이름이 다르면 web/models/avatar.json 의 "bones" 로 직접 지정: { "bones": { "leftUpperArm": "내_뼈_이름", ... } }
import * as THREE from "three";
import { VRMHumanoid } from "@pixiv/three-vrm";

const SIDE = { left: ["l", "left"], right: ["r", "right"] };

// VRM 뼈 이름 → 후보 이름들 (소문자, 접두어 'cc_base_' · 'mixamorig' · 'rl_' 뗀 뒤 비교). {s} 는 l/left 또는 r/right
const CANDIDATES = {
  hips: ["hip", "hips", "pelvis", "bip01pelvis"],
  spine: ["waist", "spine", "spine01", "bip01spine"],
  chest: ["spine01", "spine1", "spine02", "chest", "bip01spine1"],
  upperChest: ["spine02", "spine2", "upperchest", "bip01spine2"],
  neck: ["necktwist01", "neck", "neck1", "bip01neck"],
  head: ["head", "bip01head"],
  jaw: ["jawroot", "jaw", "bip01mjaw"],
  "{S}Eye": ["{s}_eye", "{s}eye", "bip01{s}eye"],
  "{S}Shoulder": ["{s}_clavicle", "{s}shoulder", "{s}_shoulder", "bip01{s}clavicle"],
  "{S}UpperArm": ["{s}_upperarm", "{s}arm", "{s}upperarm", "bip01{s}upperarm"],
  "{S}LowerArm": ["{s}_forearm", "{s}forearm", "{s}lowerarm", "bip01{s}forearm"],
  "{S}Hand": ["{s}_hand", "{s}hand", "bip01{s}hand"],
  "{S}ThumbMetacarpal": ["{s}_thumb1", "{s}handthumb1", "bip01{s}finger0"],
  "{S}ThumbProximal": ["{s}_thumb2", "{s}handthumb2", "bip01{s}finger01"],
  "{S}ThumbDistal": ["{s}_thumb3", "{s}handthumb3", "bip01{s}finger02"],
  "{S}IndexProximal": ["{s}_index1", "{s}handindex1", "bip01{s}finger1"],
  "{S}IndexIntermediate": ["{s}_index2", "{s}handindex2", "bip01{s}finger11"],
  "{S}IndexDistal": ["{s}_index3", "{s}handindex3", "bip01{s}finger12"],
  "{S}MiddleProximal": ["{s}_mid1", "{s}handmiddle1", "{s}_middle1", "bip01{s}finger2"],
  "{S}MiddleIntermediate": ["{s}_mid2", "{s}handmiddle2", "{s}_middle2", "bip01{s}finger21"],
  "{S}MiddleDistal": ["{s}_mid3", "{s}handmiddle3", "{s}_middle3", "bip01{s}finger22"],
  "{S}RingProximal": ["{s}_ring1", "{s}handring1", "bip01{s}finger3"],
  "{S}RingIntermediate": ["{s}_ring2", "{s}handring2", "bip01{s}finger31"],
  "{S}RingDistal": ["{s}_ring3", "{s}handring3", "bip01{s}finger32"],
  "{S}LittleProximal": ["{s}_pinky1", "{s}handpinky1", "bip01{s}finger4"],
  "{S}LittleIntermediate": ["{s}_pinky2", "{s}handpinky2", "bip01{s}finger41"],
  "{S}LittleDistal": ["{s}_pinky3", "{s}handpinky3", "bip01{s}finger42"],
  "{S}UpperLeg": ["{s}_thigh", "{s}upleg", "{s}upperleg", "bip01{s}thigh"],
  "{S}LowerLeg": ["{s}_calf", "{s}leg", "{s}lowerleg", "bip01{s}calf"],
  "{S}Foot": ["{s}_foot", "{s}foot", "bip01{s}foot"],
  "{S}Toes": ["{s}_toebase", "{s}toebase", "{s}toes", "bip01{s}toe0"],
};

// 비교용 이름: 소문자, 접두어 떼고, 공백·점·밑줄 제거 (three.js 는 노드 이름의 공백을 밑줄로 바꿈: 'Bip01 L Hand' → 'Bip01_L_Hand')
const norm = (n) => n.toLowerCase().replace(/^(cc_base_|rl_|mixamorig:?|armature[_|:]?)/, "").replace(/[\s._]/g, "");

export function findBones(root, overrides = {}) {
  const byName = new Map();
  root.traverse((o) => { if (o.isBone || o.type === "Object3D" || o.isObject3D) { const k = norm(o.name || ""); if (k && !byName.has(k)) byName.set(k, o); } });
  const out = {};
  for (const [vrmName, cands] of Object.entries(CANDIDATES)) {
    const variants = vrmName.includes("{S}")
      ? Object.entries(SIDE).map(([side, abbr]) => [vrmName.replace("{S}", side).replace(/^(left|right)(.)/, (_, a, b) => a + b.toUpperCase()),
          abbr.flatMap((s) => cands.map((c) => c.replace("{s}", s)))])
      : [[vrmName, cands]];
    for (const [name, list] of variants) {
      const hit = list.map((c) => byName.get(norm(c))).find((o) => o && (o.isBone || o.children.length || /eye|jaw/i.test(name)));
      if (hit) out[name] = hit;
    }
  }
  for (const [k, v] of Object.entries(overrides)) {
    let o = null;
    root.traverse((x) => { if (!o && x.name === v) o = x; });
    if (o) out[k] = o;
  }
  // 척추 마디가 모자란 모델: chest/upperChest 가 spine 과 같은 뼈면 빼기
  if (out.chest === out.spine) delete out.chest;
  if (out.upperChest === out.chest || out.upperChest === out.spine) delete out.upperChest;
  return out;
}

// 팔뚝 비틀림 뼈 (CC4: CC_Base_L_ForearmTwist01·02) — 손목 회전의 일부를 나눠 줘서 손목이 꼬여 보이지 않게
export function findTwistBones(root) {
  const res = { left: [], right: [] };
  root.traverse((o) => {
    const k = norm(o.name || "");
    const m = k.match(/^(l|r|left|right)_?forearmtwist0?(\d)?$/);
    if (m) res[m[1].startsWith("l") ? "left" : "right"].push(o);
  });
  return res;
}

// 체격 조절 (config.physique): 뼈 크기를 바꿔 몸 메시도 함께 커지게. 값은 화면 기준 [가로(x), 세로(y), 앞뒤(z)] 배율
//   waist: 허리, chest: 가슴(허리 배율에 곱해짐), neck: 목 굵기, shoulders: 어깨 너비(쇄골 길이), upperArm·forearm: 팔 굵기
// 자식 뼈는 크기를 이어받으므로, 커지면 안 되는 곳(목 위·팔 아래)은 반대로 줄여 원래 크기로 되돌림
// (기본 자세에서 뼈 축이 화면 축과 거의 나란하다고 보고, 뼈의 로컬 축마다 가장 가까운 화면 축의 배율을 씀)
function applyPhysique(bones, P, scene) {
  scene.updateMatrixWorld(true);
  const V3 = (a) => new THREE.Vector3(...(Array.isArray(a) ? a : [a, a, a]));
  const quat = new Map();
  for (const b of Object.values(bones)) quat.set(b, b.getWorldQuaternion(new THREE.Quaternion()));
  const toLocal = (bone, w) => {
    const q = quat.get(bone), out = new THREE.Vector3();
    ["x", "y", "z"].forEach((ax, i) => {
      const d = new THREE.Vector3(i === 0 ? 1 : 0, i === 1 ? 1 : 0, i === 2 ? 1 : 0).applyQuaternion(q);
      const a = [Math.abs(d.x), Math.abs(d.y), Math.abs(d.z)];
      out[ax] = [w.x, w.y, w.z][a.indexOf(Math.max(...a))];
    });
    return out;
  };
  const mul = (bone, w) => { if (bone) bone.scale.multiply(toLocal(bone, w)); };
  const inv = (w) => new THREE.Vector3(1 / w.x, 1 / w.y, 1 / w.z);
  const waist = V3(P.waist || 1), chest = V3(P.chest || 1), neck = V3(P.neck || 1);
  const trunk = waist.clone().multiply(chest);
  mul(bones.spine, waist);
  mul(bones.chest || bones.upperChest, chest);
  // 목: 몸통 배율을 되돌린 뒤 목 굵기만. 머리는 목 배율도 되돌림
  mul(bones.neck, inv(trunk).multiply(neck));
  mul(bones.head, inv(neck));
  const neckCarries = bones.leftShoulder && bones.leftShoulder.parent === bones.neck;
  for (const side of ["left", "right"]) {
    const sh = bones[side + "Shoulder"], up = bones[side + "UpperArm"], lo = bones[side + "LowerArm"], hand = bones[side + "Hand"];
    if (sh && !neckCarries) mul(sh, inv(trunk));                // 쇄골이 가슴에 붙은 뼈대: 몸통 배율 되돌림
    else if (sh) mul(sh, inv(neck));                              // 쇄골이 목에 붙은 뼈대(Biped): 목 배율 되돌림
    if (up && P.shoulders) up.position.multiplyScalar(P.shoulders);   // 어깨 너비
    const ua = V3(P.upperArm || 1), fa = V3(P.forearm || 1);
    mul(up, ua);
    mul(lo, inv(ua).multiply(fa));
    mul(hand, inv(fa));
  }
  scene.updateMatrixWorld(true);
}

// GLB 를 VRM 비슷한 객체로: { scene, humanoid, update(dt) }
export function buildRig(gltf, config = {}) {
  const scene = gltf.scene;
  scene.updateMatrixWorld(true);
  // 단위: 키가 10 이 넘으면 cm 로 보고 m 로 (FBX → GLB 에서 흔함)
  const box = new THREE.Box3().setFromObject(scene);
  const h = box.max.y - box.min.y;
  if (h > 10) scene.scale.multiplyScalar(0.01);
  scene.updateMatrixWorld(true);
  const bones = findBones(scene, config.bones || {});
  const need = ["hips", "spine", "head", "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm", "rightLowerArm", "rightHand"];
  const missing = need.filter((n) => !bones[n]);
  if (missing.length) throw new Error("아바타 뼈를 찾지 못함: " + missing.join(", ") + " (web/models/avatar.json 의 bones 로 지정)");
  if (config.physique) applyPhysique(bones, config.physique, scene);
  // 상체 비율: config.torsoScale (예: 0.9) 이면 척추 마디 사이 간격을 그만큼 줄임 (뼈를 따라 몸통 메시도 줄어듦)
  if (config.torsoScale && config.torsoScale !== 1) {
    for (const n of ["chest", "upperChest", "neck"]) if (bones[n]) bones[n].position.multiplyScalar(config.torsoScale);
    scene.updateMatrixWorld(true);
  }
  // 정면: 왼팔이 +x 쪽이어야 +z(카메라)를 보는 것 (VRM 규칙). 반대면 180° 돌림
  const lx = bones.leftUpperArm.getWorldPosition(new THREE.Vector3()).x;
  const rx = bones.rightUpperArm.getWorldPosition(new THREE.Vector3()).x;
  if (lx < rx) { scene.rotation.y += Math.PI; scene.updateMatrixWorld(true); }
  const humanBones = Object.fromEntries(Object.entries(bones).map(([k, node]) => [k, { node }]));
  const humanoid = new VRMHumanoid(humanBones, { autoUpdateHumanBones: true });
  scene.add(humanoid.normalizedHumanBonesRoot);
  return {
    scene, humanoid, isVRM: false,
    twist: findTwistBones(scene),
    update() { humanoid.update(); },
  };
}
