# avatar.glb 출처·라이선스

- 원본: Microsoft Rocketbox `Assets/Avatars/Professions/Construction_Male_07` — https://github.com/microsoft/Microsoft-Rocketbox
- 라이선스: **MIT** (Copyright (c) Microsoft Corporation). 상업적 사용·수정·재배포 허용, 저작권·라이선스 고지 유지
- 바꾼 점: `scripts/avatar/rocketbox_to_glb.py --subdivide=1 --drop-islands=40 --smooth-armpits=0.14` (Blender) 로 표정 있는 FBX → GLB, 몸(옷·손) 부분 한 단계 세분화(꼭짓점 4천 → 1만2천), 조끼 버클 조각 제거, 겨드랑이 뼈 가중치 스무딩, 셰이프키는 ARKit 52(눈동자 제외)·입 모양 15개만,
  `scripts/deploy/slim_glb.mjs` 로 텍스처 WebP 2048 (31MB → 5MB)

MIT License — Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files, to deal in the Software without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies, subject to the following conditions: The above copyright notice and
this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
WITHOUT WARRANTY OF ANY KIND.
