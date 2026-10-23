# 이음 API 명세 (프론트 · 아바타 담당용)

서버 실행 (기본 주소 `http://localhost:8000`)
```bash
KSL_CKPT=checkpoints/run1/best.pt KSL_INDEX=checkpoints/kosha_index.pkl \
KSL_LEXICON=data/sign/lexicon.csv KSL_MOTION=data/sign/motions uvicorn api:app --host 0.0.0.0 --port 8000
```
- 서버 없이 화면 개발: `docs/mock/ask_response.json` 을 `/ask` 응답 대신 불러와 쓰면 됨 (실제 응답 그대로 저장한 파일)
- CORS 전체 허용 → React 개발 서버(`localhost:5173` 등)에서 바로 호출 가능
- `--host 0.0.0.0` 이면 같은 와이파이의 휴대폰에서 `http://<노트북 IP>:8000` 으로 접속 가능

## 엔드포인트

| 메서드 | 경로 | 요청 | 용도 |
|---|---|---|---|
| POST | `/ask` | `{"question": "보안경은 언제 착용해야 하나요?"}` | 질문 → 검색 → 답변 → 수어 |
| POST | `/translate` | `{"text": "작업 전에 안전모 상태를 확인하세요."}` | 문장 직접 번역 |
| POST | `/motion` | `{"glosses": ["작업", "전", "확인"]}` | 글로스 → 동작만 |
| GET | `/demo_sentences` | – | 조원이 확정한 안내 문장 목록 |
| GET | `/health` | – | 서버 상태 |
| GET | `/` | – | 시연용 웹 화면 (`web/index.html`) |

## `/ask` 응답

```jsonc
{
  "question": "먼지 많은 작업할 때 마스크",
  "answer_mode": "demo",                // demo(준비된 안내 문장) | llm | extractive(문서 문장 그대로)
  "answer_text": "...",                 // 화면 답변: 문서 근거 (demo 이면 data/kosha/answers.json 의 답변)
  "sign_text": "분진이 많이 발생하는 작업에서는 방진마스크를 착용합니다.",  // 아바타가 수어로 보여주는 문장
  "sentences": [{
    "korean": "분진이 많이 발생하는 작업에서는 방진마스크를 착용합니다.",
    "gloss": ["있다", "작업", "FS:방진마스크", "사용"],
    "words": ["발생하는", ...],          // 화면 단어 블록 (답변에 쓰인 모양, 넣은 문법 단어는 '안 함'·'해야 함' 등)
    "nms": [null, {"roles": ["neg"], "face": {"sad": 0.3, "angry": 0.25}, "head": {"type": "shake", "n": 2, "deg": 14}}, ...],
                                        // 글로스마다 문법 표정·고개 (ksl/order.py, roles: neg|prohibit|must|emph|can|cond)
    "timeline": [{"gloss": "있다", "start": 0.0, "duration": 0.81, "nms": null}, ...],  // 동작 데이터 없을 때 쓰는 예상 시간(초)
    "lexicon_terms": [],                // 용어사전으로 번역된 전문용어
    "unverified_terms": ["방진마스크"],  // 수어 표현 미확보 → 화면에 '지문자/확인 필요' 표시 권장
    "dropped_terms": [],                // 모델이 빠뜨려서 보정한 용어 (디버그용)
    "has_unk": false,
    "method": "memory",                 // memory(확정 글로스) | model(번역 모델)
    "complete": true                    // false: 일부 단어 동작만 확보
  }],
  "motion": {                           // 동작사전(KSL_MOTION)이 없으면 null
    "fps": 30,
    "frames": [ {"body": [[x,y] x8], "rh": [[x,y] x21] | null, "lh": [...] | null}, ... ],
    "segments": [{"gloss": "있다", "start": 0, "end": 24, "has_motion": true, "nms": null}, ...]   // 프레임 번호, nms 는 위와 같음
  },
  "sources": [{"doc": "A-G-12-2026", "title": "개인보호구의 사용 및 관리에 관한 기술지원규정",
               "page": 4, "score": 0.20, "text": "문장1\n문장2"}]  // 출처: 같은 쪽은 합침, 앞 번호 표시 제거 (제목은 data/kosha/titles.json)
}
```

## 동작(`motion`) 좌표 규칙 — 아바타 연결 시 필독

- `body` 8개 순서: 코, 목, **오른**어깨, 오른팔꿈치, 오른손목, **왼**어깨, 왼팔꿈치, 왼손목
- `rh` / `lh`: 수어자 **본인 기준** 오른손 / 왼손, 21점 (MediaPipe·OpenPose 손 순서 동일)
  - 0 손목 · 1–4 엄지 · 5–8 검지 · 9–12 중지 · 13–16 약지 · 17–20 소지
- 좌표: **목 = (0,0), 어깨너비 = 1**, x 오른쪽 +, **y 아래쪽 +** (영상 좌표계)
  - 화면 정면에서 보면 수어자의 오른손은 화면 왼쪽(x 음수)에 있음
  - Three.js 로 옮길 때: `y_three = -y`, 아바타 어깨너비로 곱하기
- 점이 안 보인 프레임은 `null`
- `segments[].has_motion = false` → 동작 데이터 없는 글로스: 프레임 없이 건너뜀 (`start`/`end` = null). 화면에서는 회색 단어로만 표시
- 글로스 사이에는 0.2초 보간 프레임이 들어가 있음 (segments 사이 빈 번호 구간)

## 3D 아바타 (web/avatar.js)

- 좌표가 3개(`[x, y, z]`)면 3D: AI Hub 3D 키포인트(mm, 카메라 좌표계)를 목=원점·어깨너비=1로 정규화한 값. z 는 카메라에서 멀어지는 쪽이 +
- 변환: three.js 방향 = `(x, -y, -z)`
- 팔: 어깨→팔꿈치, 팔꿈치→손목 방향으로 상완·전완 회전 / 손목: 손목→중지 뿌리 + 검지 뿌리→소지 뿌리 두 축으로 손바닥 방향까지 / 손가락: 마디 방향
- 결과 확인: 데이터의 뼈 방향과 렌더링된 아바타 뼈 방향이 소수 둘째 자리까지 일치함을 확인
- React 로 옮길 때 `SignAvatar` 클래스를 그대로 가져가 `setFrame(motion.frames[i])` 만 호출하면 됨
