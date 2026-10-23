// 아바타 공통 상수: 손가락 마디 이름(VRM 사람 뼈 이름) ↔ 손 키포인트 번호, 좌우
import * as THREE from "three";

// [마디 이름 3개, 키포인트 번호 4개(뿌리 → 끝)]  엄지는 MediaPipe 1~4
export const FINGERS = [
  [["ThumbMetacarpal", "ThumbProximal", "ThumbDistal"], [1, 2, 3, 4]],
  [["IndexProximal", "IndexIntermediate", "IndexDistal"], [5, 6, 7, 8]],
  [["MiddleProximal", "MiddleIntermediate", "MiddleDistal"], [9, 10, 11, 12]],
  [["RingProximal", "RingIntermediate", "RingDistal"], [13, 14, 15, 16]],
  [["LittleProximal", "LittleIntermediate", "LittleDistal"], [17, 18, 19, 20]],
];
export const SIDES = [
  { pre: "right", sho: 2, elb: 3, wri: 4, hand: "rh" },   // 수어자 오른손 = 아바타 오른손
  { pre: "left", sho: 5, elb: 6, wri: 7, hand: "lh" },
];

// 데이터 좌표 → three.js 월드 방향 (y 위, z 카메라 쪽)
export const V = (p) => new THREE.Vector3(p[0], -p[1], -(p[2] || 0));
