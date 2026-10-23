// 예전 VRM(애니메이션풍) 아바타 전용 꾸미기: 장갑 벗기기·작업복·안전모·머리 다듬기 등.
// 지금 기본 아바타는 반실사 GLB(web/models/avatar.glb). VRM 은 ?avatar=vrm 으로 비교할 때만 씀.
// SignAvatar 에 메서드로 붙여 씀 (avatar.js 의 mixin).
import * as THREE from "three";
import { VRMSpringBoneCollider, VRMSpringBoneColliderShapeCapsule, VRMSpringBoneColliderShapeSphere } from "@pixiv/three-vrm";
import { FINGERS, SIDES } from "./avatar-common.js";

export class VRMLook {

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
      // 아래팔: 소매 안쪽(팔꿈치 ~ 소매 끝 조금 앞)의 피부도 숨김 → 팔을 굽혀도 팔꿈치 피부가 소매 밖으로 비치지 않음
      this.vrm.update(0); this.vrm.scene.updateMatrixWorld(true);
      const V3 = THREE.Vector3, arm = {};
      for (const P of ["left", "right"]) {
        const e = h.getRawBoneNode(P + "LowerArm"), w = h.getRawBoneNode(P + "Hand");
        if (e && w) arm[e.uuid] = [e.getWorldPosition(new V3()), w.getWorldPosition(new V3())];
      }
      const inSleeve = (k) => {
        const b = main(k), A = b && arm[b.uuid];
        if (!A) return false;
        const p = o.getVertexPosition(k, new V3()).applyMatrix4(o.matrixWorld), ax = A[1].clone().sub(A[0]);
        return p.sub(A[0]).dot(ax) / ax.lengthSq() < 0.7;
      };
      const keep = [];
      // 그룹(재질별 범위)을 유지하며 가려진 삼각형만 뺌
      const groups = g.groups.length ? g.groups : [{ start: 0, count: idx.count, materialIndex: 0 }];
      const ng = [];
      for (const gr of groups) {
        const start = keep.length;
        for (let t = gr.start; t < gr.start + gr.count; t += 3) {
          const tri = [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)];
          if (tri.every((k) => covered.has(main(k)) || inSleeve(k))) continue;
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
      let moved = 0;
      for (let k = 0; k < pos.count; k++) {
        const p = o.getVertexPosition(k, new V()).applyMatrix4(o.matrixWorld);
        const ai = p.distanceTo(arms[0].w) < p.distanceTo(arms[1].w) ? 0 : 1, A = arms[ai];
        const ax = A.w.clone().sub(A.e), t = p.clone().sub(A.e).dot(ax) / ax.lengthSq();
        if (t <= end) {
          // 소매 끝 가까운 옷 꼭짓점이 손(손목) 뼈를 조금이라도 따라가면, 손목을 꺾을 때 소매 끝이 검은 조각처럼 튀어나옴 → 아래팔만 따르게
          if (t > end - 0.3 && p.clone().sub(A.e.clone().addScaledVector(ax, t)).length() < ax.length() * 0.9) {
            const hb = bones.indexOf(h.getRawBoneNode(ai === 0 ? "leftHand" : "rightHand"));
            const lb = bones.indexOf(A.bone);
            let hw = 0;
            for (let j = 0; j < 4; j++) if (si.getComponent(k, j) === hb) hw += sw.getComponent(k, j);
            if (hw > 0 && lb >= 0) {
              const ws = [0, 1, 2, 3].map((j) => [si.getComponent(k, j), sw.getComponent(k, j)]).filter(([b]) => b !== hb);
              let lw = hw; const out = [];
              for (const [b, w] of ws) { if (b === lb) lw += w; else if (w > 0) out.push([b, w]); }
              out.unshift([lb, lw]);
              while (out.length < 4) out.push([0, 0]);
              si.setXYZW(k, out[0][0], out[1][0], out[2][0], out[3][0]);
              sw.setXYZW(k, out[0][1], out[1][1], out[2][1], out[3][1]);
              moved++;
            }
          }
          continue;
        }
        const radial = p.clone().sub(A.e.clone().addScaledVector(ax, t));
        if (radial.length() > ax.length() * 0.9) continue;          // 아래팔과 먼 꼭짓점(몸통 등)은 제외
        info.push({ k, ai, A, ax, t, radial });
        if (t < 1.0) maxR[ai] = Math.max(maxR[ai], radial.length());
      }
      // 2차: 소매(굵은 부분)는 end 지점 둘레로 당기고, 손목에 딱 붙는 장갑·손 부분은 소매 안쪽 축으로 접어 넣음
      const folded = new Set();
      for (const { k, ai, A, ax, t, radial } of info) {
        const tight = t > 1.0 || radial.length() < maxR[ai] * 0.8;
        if (tight) folded.add(k);
        // 장갑·손목 부분은 소매 끝 바로 안쪽의 좁은 고리로 접음 (축 한 점으로 모으면 소매 안에 검은 원뿔 조각이 생김)
        const target = tight ? A.e.clone().addScaledVector(ax, end - 0.02).add(radial.clone().setLength(Math.max(0.005, maxR[ai] * 0.8)))
                             : A.e.clone().addScaledVector(ax, end).add(radial);
        const local = target.applyMatrix4(inv);
        pos.setXYZ(k, local.x, local.y, local.z);
        if (tight && g.attributes.normal) {        // 접어 넣은 고리는 바깥쪽을 보게 → 소매 끝 안쪽이 검게 보이지 않음
          const nrm = radial.clone().normalize().transformDirection(inv);
          g.attributes.normal.setXYZ(k, nrm.x, nrm.y, nrm.z); g.attributes.normal.needsUpdate = true;
        }
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
          // 얼굴 윤곽은 또렷하게, 몸(손 포함) 피부는 얇게: 손가락을 굽히면 굵은 윤곽선이 손등·마디에 검게 비쳐 보임
          // 작업복 윤곽선도 얇게: 팔꿈치를 굽히면 윤곽선 면이 소매 밖으로 검은 뾰족한 조각처럼 튀어나옴
          m.outlineWidthFactor *= /SKIN/i.test(m.name) ? (/Body/i.test(m.name) ? 0.5 : 1.8) : (/Tops/i.test(m.name) ? 0.6 : 1.3);
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
}
