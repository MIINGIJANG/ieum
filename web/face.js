// 얼굴: ARKit 52 표정(blend shape) 이름으로 통일해서 다룸.
//  - GLB(CC4·MPFB 등): 메시의 모프 타깃을 이름으로 찾아 직접 움직임
//  - VRM: ARKit 이름 → VRM 기본 표정(aa·ou·angry·surprised …)으로 바꿔서 씀
// 한 프레임 동안 set() 으로 목표값을 모은 뒤 apply(dt) 에서 부드럽게 반영 (값은 큰 것끼리 합침)

const VRM_MAP = {   // ARKit → VRM 기본 표정 (비슷한 것끼리)
  browInnerUp: "surprised", browOuterUpLeft: "surprised", browOuterUpRight: "surprised", eyeWideLeft: "surprised", eyeWideRight: "surprised",
  browDownLeft: "angry", browDownRight: "angry", eyeSquintLeft: "angry", eyeSquintRight: "angry", noseSneerLeft: "angry", noseSneerRight: "angry",
  mouthFrownLeft: "sad", mouthFrownRight: "sad",
  mouthSmileLeft: "relaxed", mouthSmileRight: "relaxed",
  eyeBlinkLeft: "blinkLeft", eyeBlinkRight: "blinkRight",
  jawOpen: "aa", viseme_aa: "aa", viseme_O: "oh", mouthFunnel: "oh", viseme_U: "ou", mouthPucker: "ou",
  viseme_I: "ih", viseme_E: "ee", mouthStretchLeft: "ee", mouthStretchRight: "ee",
};

// 모프가 없는 모델을 위한 대체 (입 모양 viseme → 기본 ARKit 표정 조합)
const FALLBACK = {
  viseme_aa: { jawOpen: 0.55, mouthFunnel: 0.1 },
  viseme_O: { jawOpen: 0.3, mouthFunnel: 0.55 },
  viseme_U: { jawOpen: 0.1, mouthPucker: 0.75 },
  viseme_E: { jawOpen: 0.2, mouthStretchLeft: 0.4, mouthStretchRight: 0.4 },
  viseme_I: { jawOpen: 0.08, mouthStretchLeft: 0.5, mouthStretchRight: 0.5 },
  viseme_PP: { mouthClose: 0.5, mouthPressLeft: 0.4, mouthPressRight: 0.4 },
};

export class FaceRig {
  constructor(root, vrm = null) {
    this.vrm = vrm;
    this.binds = new Map();         // ARKit 이름 → [[mesh, index]]
    this.cur = new Map();
    this.tgt = new Map();
    if (!vrm) {
      root.traverse((o) => {
        if (!o.isMesh || !o.morphTargetDictionary) return;
        for (const [name, i] of Object.entries(o.morphTargetDictionary)) {
          const k = name.replace(/^.*[.:]/, "");              // 'Face.browInnerUp' 같은 접두어 떼기
          if (!this.binds.has(k)) this.binds.set(k, []);
          this.binds.get(k).push([o, i]);
        }
      });
    }
    this._vrmName = {};
  }

  has(name) {
    if (this.vrm) return !!this._vrmKey(VRM_MAP[name]);
    return this.binds.has(name);
  }

  // 목표값 (같은 프레임에 여러 번 부르면 큰 값)
  set(name, v) {
    if (!(v > 0)) return;
    if (!this.vrm && !this.binds.has(name) && FALLBACK[name]) {
      for (const [k, w] of Object.entries(FALLBACK[name])) this.set(k, v * w);
      return;
    }
    this.tgt.set(name, Math.max(this.tgt.get(name) || 0, Math.min(1, v)));
  }

  _vrmKey(name) {
    if (!name) return null;
    if (!(name in this._vrmName)) {
      const all = Object.keys(this.vrm.expressionManager?.expressionMap || {});
      this._vrmName[name] = all.find((k) => k.toLowerCase() === name.toLowerCase()) || null;
    }
    return this._vrmName[name];
  }

  // 부드럽게 반영: 올라갈 때 tauUp, 내려갈 때 tauDown (눈 깜빡임은 빠르게)
  apply(dt) {
    const names = new Set([...this.cur.keys(), ...this.tgt.keys()]);
    for (const n of names) {
      const t = this.tgt.get(n) || 0, c = this.cur.get(n) || 0;
      const fast = /Blink/.test(n), mouth = /^(viseme|jaw|mouth)/.test(n);
      const tau = fast ? 0.03 : mouth ? 0.05 : t > c ? 0.08 : 0.16;
      const v = c + (t - c) * (1 - Math.exp(-dt / tau));
      if (v < 1e-3 && t === 0) this.cur.delete(n); else this.cur.set(n, v);
    }
    this.tgt.clear();
    if (this.vrm) {
      const em = this.vrm.expressionManager;
      if (!em) return;
      const agg = {};
      for (const [n, v] of this.cur) {
        const key = this._vrmKey(VRM_MAP[n]);
        if (key) agg[key] = Math.max(agg[key] || 0, v);
      }
      for (const k of Object.keys(em.expressionMap || {})) if (!/^(happy|neutral)$/i.test(k)) em.setValue(k, agg[k] || 0);
      return;
    }
    for (const [n, list] of this.binds) {
      const v = this.cur.get(n) || 0;
      for (const [mesh, i] of list) mesh.morphTargetInfluences[i] = v;
    }
  }
}

// ---------------- 입 모양 (한국수어의 '입 모양': 단어를 입으로 함께 말함) ----------------
// 한글 음절 → 모음(중성)으로 입 모양, 첫소리·받침이 ㅁ·ㅂ·ㅍ 이면 입술을 잠깐 닫음
const JUNG_VISEME = ["aa", "E", "aa", "E", "O", "E", "O", "E", "O", "aa", "E", "E", "O", "U", "O", "E", "U", "U", "I", "I", "I"];
const LIP_CHO = new Set([6, 7, 8, 17]);     // ㅁ ㅂ ㅃ ㅍ
const LIP_JONG = new Set([16, 17, 26]);     // ㅁ ㅂ ㅍ

export function mouthSyllables(text) {
  const out = [];
  for (const ch of text || "") {
    const c = ch.charCodeAt(0) - 0xac00;
    if (c < 0 || c > 11171) continue;
    const cho = Math.floor(c / 588), jung = Math.floor((c % 588) / 28), jong = c % 28;
    out.push({ v: "viseme_" + JUNG_VISEME[jung], lipIn: LIP_CHO.has(cho), lipOut: LIP_JONG.has(jong) });
  }
  return out;
}

// 단어 진행도 t(0~1) 에서 입 모양 값들: 음절을 단어 시간의 85% 에 나눠 배치
export function mouthAt(syl, t, strength = 0.55) {
  const res = {};
  if (!syl.length || t < 0 || t > 0.9) return res;
  const span = 0.85 / syl.length;
  const i = Math.min(syl.length - 1, Math.floor(t / span));
  const u = (t - i * span) / span;                         // 음절 안 진행도
  const s = syl[i];
  const env = Math.sin(Math.PI * Math.min(1, u * 1.1)) ** 0.7;
  if (s.lipIn && u < 0.22) res.viseme_PP = strength * (1 - u / 0.22);
  else if (s.lipOut && u > 0.75) res.viseme_PP = strength * (u - 0.75) / 0.25;
  else res[s.v] = strength * env;
  return res;
}
