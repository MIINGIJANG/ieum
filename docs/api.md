# 이음 API

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

- 브라우저: `http://localhost:8000` (서비스 화면), `/docs` (API 테스트 화면)
- CORS 전체 허용 → 다른 개발 서버(`localhost:5173` 등)에서 바로 호출 가능
- `--host 0.0.0.0` 이면 같은 와이파이의 휴대폰에서 `http://<노트북 IP>:8000`
- 서버 없이 화면 개발: `docs/mock/ask_response.json` (실제 `/ask` 응답을 저장한 파일, 필드는 예전 형식일 수 있음)

## 환경 변수

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `KSL_INDEX` | `checkpoints/kosha_index.pkl` | 검색 인덱스 (없으면 `data/kosha/*.pdf` 로 만듦) |
| `KSL_MOTION` | `data/sign/motions` | 동작사전 |
| `KSL_CKPT` | `checkpoints/run1/best.pt` | 번역 모델 (문장 직접 번역에만) |
| `KSL_LEXICON`, `KSL_SENTENCES` | `data/sign/lexicon.csv`, `data/sign/demo_sentences.json` | 용어사전, 안내 문장 |
| `ANTHROPIC_API_KEY` | — | 있으면 Claude 답변 모드 (`LLM_MODEL` 로 모델 지정) |
| `KSL_KEYPOINTS` | `rule` | `llm` 이면 Claude 가 핵심 낱말을 고르는 방식도 시도 (`KSL_KEYPOINT_MODEL`) |
| `KSL_FINGERSPELL`, `KSL_FS_LIMIT` | 꺼짐, 3 | 지문자 켜기, 답변마다 최대 개수 |
| `KSL_MISSING_LOG` | `data/logs/missing_glosses.json` | 동작 없는 단어 기록 파일 |
| `KSL_HF_TOKEN`(또는 `HF_TOKEN`), `KSL_LOG_REPO`, `KSL_LOG_SYNC_SEC` | —, `MIINGI/ieum-logs`, 600 | 기록을 Hugging Face 데이터셋에 보관 (Space 위에서만) |
| `KSL_NO_WARMUP` | — | 켜질 때 예시 질문 답 미리 만들기·웹 파일 미리 압축 건너뜀 (개발용) |
| `DATA_GO_KR_KEY`, `KAKAO_REST_KEY`, `KAKAO_JS_KEY` | — | SOS: 공공데이터포털 키, 카카오 REST 키(경로·장소), 카카오 지도 JavaScript 키 (`docs/sos.md`) |
| `IEUM_GZ_DIR` | 임시 폴더의 `ieum-gz` | 웹 파일 압축본을 두는 곳 |

## 엔드포인트

| 메서드 | 경로 | 요청 | 용도 |
|---|---|---|---|
| POST | `/query` | `{"text": "...", "prefetch": false, "seen": false}` | **화면이 쓰는 것.** 모든 입력을 질문으로 보고 답함 (산업안전과 무관하면 `mode: none`). 같은 질문은 캐시로 바로. `prefetch` 는 미리 받기(기록 안 함), `seen` 은 미리 받은 답을 보였다는 기록만 |
| POST | `/ask` | `{"question": "..."}` | 질문 → 검색 → 답변 → 수어 |
| POST | `/motion` | `{"glosses": ["위험1", "조심1"]}` | 글로스 → 이어붙인 동작 |
| POST | `/motions` | `{"glosses": [...]}` | 글로스마다 따로 만든 동작 (블록을 눌러 다시 볼 때, 최대 40개) |
| GET | `/examples` | – | 예시 질문 + 주제 키 |
| GET | `/pages` | – | 출처 쪽 이미지 목록 (화면이 미리 받아 둠) |
| POST | `/warmup` | – | 첫 화면 로딩 중 한 번: 번역 모델·검색·동작을 미리 돌려 둠 |
| GET | `/demo_sentences` | – | 조원이 확정한 안내 문장 |
| GET | `/blocks_check` | – | 예시 질문마다 블록·점검 결과 (화면 `/blocks.html`) |
| GET | `/missing?n=100` | – | 동작 없는 단어·대체 표현 기록 + 보관 상태 (화면 `/missing.html`) |
| GET | `/eval?detail=false` | – | 환각 방지 평가를 이 서버로 실행 |
| GET | `/llm_check` | – | Claude 연결 확인 (질문 1번 비용) |
| GET | `/health` | – | 서버 상태 |
| GET | `/sos/config` | – | SOS 준비 상태 (지도용 공개 키, 서버 키가 있는지) |
| GET | `/sos/nearest?lat=&lng=&fresh=1` | – | 가까운 지정 응급의료기관 + 자동차 경로 + 병상 + 진료 가능 정보 (`docs/sos.md`). 위치는 저장하지 않음 |
| GET | `/sos/status` | – | SOS 사용 기록·최근 오류 (위치 없음) |
| POST | `/client/log` | `{"kind": "boot", "times": {...}}` 또는 `{"kind": "error", "msg": "..."}` | 화면에서 난 오류·첫 로딩 시간 기록 (`ksl/clientlog.py`). 주소(IP)·위치·질문 내용은 남기지 않음 |
| GET | `/client/status` | – | 기기 분류별 첫 로딩 시간(가운데 값·가장 긴 값), 오류 건수, 최근 오류 50건 (서버가 다시 켜지면 처음부터) |
| GET | `/offline/manifest` | – | 웹 파일 목록과 내용 해시 (오프라인 저장용, `docs/offline.md`) |

웹 파일(`web/`)은 한 번 압축해 둔 것을 보내고 모두 `Cache-Control: no-cache` (바뀌지 않았으면 304) — `ksl/webfiles.py`.

## `/ask`·`/query` 응답

```jsonc
{
  "mode": "ask",                         // /query: ask(질문) | none(산업안전과 무관해 답하지 않음)
  "question": "낙하물은 어떻게 조심해요?",
  "answer_mode": "demo",                 // demo(모범 답안) | llm(Claude) | extractive(문서 문장 그대로)
  "answer_text": "...",                  // 화면 답변 (비어 있으면 답하지 않음)
  "sign_text": "물체가 떨어질 위험이 있으면 안전모와 안전화를 착용해야 합니다.",   // 아바타가 표현하는 문장
  "sign_debug": {"hint": "...", "used": "sign", "reviewed": false, "reject": null},   // 수어 문장을 어떻게 골랐는지 (점검용)
  "sentences": [{
    "korean": "...",
    "gloss": ["물건1", "떨어지다0", ...],  // 수어 낱말 (재생 순서)
    "words": ["물체가", "떨어질", ...],    // 화면 블록 글자 (빈 칸 = 앞 블록에 이어지는 동작)
    "group": [null, ...],                // 같은 값이면 블록 하나 (숫자+단위)
    "sub_of": [null, ...],               // 대체 표현이면 원래 낱말
    "nms": [null, {"roles": ["cond"], "face": {...}, "head": {...}}, ...],   // 문법 표정·고개 (ksl/order.py)
    "timeline": [{"gloss": "...", "start": 0.0, "duration": 0.8, "nms": null}, ...],
    "method": "keypoints_sign",          // keypoints_sign | keypoints_sentence | keypoints_rule | extract | memory | model
    "complete": false                    // 모든 낱말에 동작이 있는지
  }],
  "motion": {"fps": 30, "frames": [...], "segments": [{"gloss": "...", "start": 0, "end": 24, "has_motion": true, "nms": null}, ...]},
  "sources": [{"doc": "A-G-12-2026", "title": "개인보호구의 사용 및 관리에 관한 기술지원규정", "page": 5, "score": 0.2,
               "text": "...", "blocks": [...], "region": [...]}]    // 같은 쪽은 합침, region 은 원본 PDF 에서 잘라 보여줄 영역
  "top_score": 0.08                      // mode 가 none 일 때: 검색 최고 점수
}
```

## 동작 좌표 (아바타 연결 시 필독)

- 프레임: `body` 8점(코, 목, 오른어깨, 오른팔꿈치, 오른손목, 왼어깨, 왼팔꿈치, 왼손목) + `rh`/`lh` 21점(수어자 본인 기준)
- 목 = (0,0,0), 어깨너비 = 1, x 오른쪽 +, **y 아래 +**, z 카메라에서 멀어지는 쪽 + → three.js 로는 `(x, -y, -z)`
- 화면 정면에서 보면 수어자의 오른손은 화면 왼쪽(x 음수)
- 점이 안 보이면 `null`
- `segments[].has_motion = false`: 동작 없는 낱말 → 프레임 없이 건너뜀 (`start`/`end` = null), 화면은 회색 블록
- 낱말 사이 연결 프레임에는 `tr = {"a": 앞 프레임, "b": 뒤 프레임, "t": 0~1}` (아바타가 두 자세의 뼈 회전을 섞음)
- 지문자를 켜면 그 segment 에 `fingerspell: true`, `fs_text`, `fs_verified`

자세한 형식·가져오기는 `docs/motion_data.md`, 아바타는 `docs/avatar.md`.
