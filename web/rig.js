// 일반 GLB 캐릭터(Character Creator 4, Mixamo/MPFB/Ready Player Me 이름 규칙 등) → VRM 과 같은 '사람 뼈대'로 묶기.
// three-vrm 의 VRMHumanoid 를 그대로 써서 '정규화 뼈대'(기본 자세에서 모든 회전 0)를 만들면
// 아바타 코드(avatar.js)는 VRM 이든 CC4 든 같은 방식으로 움직일 수 있다.
//
// 뼈 이름이 다르면 web/models/avatar.json 의 "bones" 로 직접 지정: { "bones": { "leftUpperArm": "내_뼈_이름", ... } }
import * as THREE from "three";
import { VRMHumanoid } from "@pixiv/three-vrm";

const SIDE = { left: ["l", "left"], right: ["r", "right"] };

// VRM 뼈 이름 → 후보 이름들 (소문자, 접두어 'cc_base_' · 'mixamorig' · 'rl_' 뗀 뒤 비교). {s} 는 l/left 또는 r/right
const CANDIDATES = {
  hips: ["hip", "hips", "pelvis"],
  spine: ["waist", "spine", "spine01"],
  chest: ["spine01", "spine1", "spine02", "chest"],
  upperChest: ["spine02", "spine2", "upperchest"],
  neck: ["necktwist01", "neck", "neck1"],
  head: ["head"],
  jaw: ["jawroot", "jaw"],
  "{S}Eye": ["{s}_eye", "{s}eye"],
  "{S}Shoulder": ["{s}_clavicle", "{s}shoulder", "{s}_shoulder"],
  "{S}UpperArm": ["{s}_upperarm", "{s}arm", "{s}upperarm"],
  "{S}LowerArm": ["{s}_forearm", "{s}forearm", "{s}lowerarm"],
  "{S}Hand": ["{s}_hand", "{s}hand"],
  "{S}ThumbMetacarpal": ["{s}_thumb1", "{s}handthumb1"],
  "{S}ThumbProximal": ["{s}_thumb2", "{s}handthumb2"],
  "{S}ThumbDistal": ["{s}_thumb3", "{s}handthumb3"],
  "{S}IndexProximal": ["{s}_index1", "{s}handindex1"],
  "{S}IndexIntermediate": ["{s}_index2", "{s}handindex2"],
  "{S}IndexDistal": ["{s}_index3", "{s}handindex3"],
  "{S}MiddleProximal": ["{s}_mid1", "{s}handmiddle1", "{s}_middle1"],
  "{S}MiddleIntermediate": ["{s}_mid2", "{s}handmiddle2", "{s}_middle2"],
  "{S}MiddleDistal": ["{s}_mid3", "{s}handmiddle3", "{s}_middle3"],
  "{S}RingProximal": ["{s}_ring1", "{s}handring1"],
  "{S}RingIntermediate": ["{s}_ring2", "{s}handring2"],
  "{S}RingDistal": ["{s}_ring3", "{s}handring3"],
  "{S}LittleProximal": ["{s}_pinky1", "{s}handpinky1"],
  "{S}LittleIntermediate": ["{s}_pinky2", "{s}handpinky2"],
  "{S}LittleDistal": ["{s}_pinky3", "{s}handpinky3"],
  "{S}UpperLeg": ["{s}_thigh", "{s}upleg", "{s}upperleg"],
  "{S}LowerLeg": ["{s}_calf", "{s}leg", "{s}lowerleg"],
  "{S}Foot": ["{s}_foot", "{s}foot"],
  "{S}Toes": ["{s}_toebase", "{s}toebase", "{s}toes"],
};

const norm = (n) => n.toLowerCase().replace(/^(cc_base_|rl_|mixamorig:?|armature[_|:]?)/, "").replace(/[\s.]/g, "");

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
      const hit = list.map((c) => byName.get(c)).find((o) => o && (o.isBone || o.children.length || /eye|jaw/i.test(name)));
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
