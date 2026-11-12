# 이음 — 청각장애 근로자를 위한 산업안전 수어 안내

질문하면 KOSHA 「개인보호구의 사용 및 관리에 관한 기술지원규정」에서 근거를 찾아 답하고, 그 답을 3D 아바타가 한국수어로 보여주는 서비스입니다.
응급 상황에는 통화 없이 가장 가까운 응급실과 가는 길을 바로 보여줍니다. 영남대학교 컴퓨터공학과 팀 프로젝트.

- 서비스: https://miingi-ieum.hf.space (Hugging Face Space, `main` 에 올리면 자동 배포)
- 수어 블록 점검: `/blocks.html` · 동작 없는 단어 기록: `/missing.html`

## 왜 만들었나

**안전 정보를 수어로.** 태어날 때부터 청각장애가 있는 사람(농인)에게 한국어 글은 제2언어에 가깝습니다. 뉴스에 자막이 있어도 수어 통역을 따로 띄우는 이유입니다.
2026년 2월 「방송통신발전 기본법」 개정으로 재난방송에 한국수어 사용이 의무·권고가 되었지만, 작업 현장의 안전 정보(보호구 착용·관리 규정)는 여전히 글로 된 설명서 위주입니다.
그래서 글 대신 수어로 안전 정보를 전하는 서비스를 만들었습니다.

**응급 상황에는 통화 없이.** 사고가 나면 보통 전화로 신고하고 구조를 요청하지만, 청각장애인은 음성 통화로 상황을 설명하거나 안내를 듣기 어렵습니다.
어디로 가야 하는지, 그 병원이 지금 환자를 받을 수 있는지를 전화로 물어볼 수도 없습니다.
그래서 응급 상황에 필요한 정보를 **통화 없이 화면으로** 주고 싶었습니다. SOS 버튼을 누르면 지금 위치에서 차로 가장 빨리 닿는 응급실,
가는 길, 응급실 남은 병상, 병원이 올린 진료 제한, 진료과별 진료 가능 여부를 한 화면에 보여줍니다.

## 할 수 있는 것

| 기능 | 내용 |
|---|---|
| 질문 → 답 → 수어 | 글·음성·카메라 수어로 질문하면 규정에서 근거를 찾아 답하고, 3D 아바타가 한국수어로 보여줌. 근거가 약하면 답하지 않음 |
| 카메라 수어 입력 | 카메라 앞에서 수어를 하면 낱말을 알아듣고 비슷한 질문을 찾아 줌 (영상은 기기 밖으로 나가지 않음) — [docs/sign_input.md](docs/sign_input.md) |
| SOS | 가까운 지정 응급의료기관·자동차 경로·실시간 병상·진료 제한·진료과별 진료 가능 정보 (위치는 저장하지 않음) — [docs/sos.md](docs/sos.md) |
| 오프라인 | 한 번 접속한 기기는 화면·아바타·수어 인식 모델·예시 질문의 답을 저장해 두고 인터넷이 없어도 동작 — [docs/offline.md](docs/offline.md) |

## 동작 방식

```
질문 ─▶ 문서 검색 (글자 TF-IDF + 단어 BM25, 상위 3개)
     ─▶ 근거가 약하면 답하지 않음 (환각 방지)
     ─▶ 답변: 준비된 모범 답안 / Claude (문서 근거 안에서만) / 문서 문장 그대로
     ─▶ 수어 문장: 답변을 짧고 완성된 한 문장으로 (사람이 쓴 문장 또는 Claude 2단계 + 검수)
     ─▶ 수어 블록 (낱말 → 글로스, 숫자·시간 수어, 대체 표현) ─▶ 동작 이어붙이기 ─▶ 3D 아바타
     └─ 출처: 근거가 된 규정 쪽을 원본 PDF 모양 그대로
```

## 빠른 시작

```bash
pip install -r requirements.txt
uvicorn api:app --port 8000        # 브라우저에서 http://localhost:8000
```

- 검색 인덱스(`checkpoints/kosha_index.pkl`)가 없으면 서버가 켜질 때 `data/kosha/*.pdf` 로 만듭니다.
- Mac 에서는 `scripts/run/start_local.command` 를 더블클릭하면 서버를 켜고 브라우저를 엽니다 (`start_public.command` 는 임시 공개 주소까지).
- `ANTHROPIC_API_KEY` 가 있으면 Claude 답변 모드, 없으면 문서 문장을 그대로 뽑아 답합니다 (환각 없음).

## 폴더 구조

```
ieum/
├── api.py                  서버 (FastAPI) — 엔드포인트는 docs/api.md
├── requirements.txt
├── ksl/                    핵심 코드
│   ├── rag.py              문서 나누기·검색·답변 (추출식 / Claude)
│   ├── pipeline.py         질문 → 답변 → 수어 블록 → 동작 (전체 연결), 숫자·시간 수어, 문장 끝 쉼
│   ├── keypoints.py        답변 → 수어 문장 (사람이 쓴 문장 / Claude 2단계·검수 / 규칙), 블록 완결성 점검
│   ├── order.py            수어 어순·문법 표정 (부정·필요·가능·조건)
│   ├── motion.py           동작 형식·정리·이어붙이기·2D→3D 깊이 추정
│   ├── fingerspell.py      지문자 (기본 꺼짐)
│   ├── sos.py              SOS: 가까운 응급실·경로·병상·진료 가능 정보 (공공데이터포털 + 카카오)
│   ├── webfiles.py         웹 파일 보내기 (미리 압축) + 오프라인 저장용 파일 목록
│   ├── clientlog.py        화면에서 난 오류·첫 로딩 시간 기록 (개인 정보 없음, /client/status)
│   ├── guard.py            남용 막기: 요청 한도, Claude 하루 한도
│   ├── rowtext.py          표의 한 줄 → 문장 (Claude 없이 답할 때)
│   ├── missing.py, logsync.py   동작 없는 단어 기록 + Hugging Face 데이터셋 보관
│   ├── pageimage.py, tables.py  출처를 원본 PDF 모양으로
│   ├── evaluation.py       환각 방지 평가 (서버 /eval, CI)
│   └── translate.py, model.py, tokenizer.py, oov.py, data.py, config.py, metrics.py   한국어→글로스 번역 모델 (docs/translation_model.md)
├── web/                    화면 (빌드 없이 그대로 서빙)
│   ├── index.html          서비스 화면 (라이트·다크 모드), 첫 로딩에서 아바타·모델·예시 답을 모두 준비
│   ├── sos.js              SOS 팝업 (지도·응급실 정보)
│   ├── sw.js               오프라인 모드 (서비스 워커) — docs/offline.md
│   ├── avatar.js           3D 수어 아바타 — rig.js(뼈 이름 인식), face.js(표정·입 모양), avatar-common.js, avatar-vrm.js
│   ├── handshapes.json     손 모양 군집·손가락 시너지 (scripts/data/build_handshapes.py)
│   ├── blocks.html, missing.html   점검 페이지
│   ├── sign/               카메라 수어 입력 (본 화면 입력창의 카메라 버튼, 시험 페이지 /sign/) — docs/sign_input.md
│   ├── models/             아바타 (avatar.glb: Microsoft Rocketbox, MIT / avatar.vrm: 예전 아바타)
│   ├── pages/              출처용 PDF 쪽 이미지
│   └── vendor/             three.js 등 라이브러리 (인터넷 없이 동작)
├── data/
│   ├── kosha/              규정 PDF·쪽 배치, 모범 답안(answers.json), 예시 질문, 산업안전 핵심어
│   ├── sign/               수어: motions/(단어별 동작 220개), sign_words.csv(말→글로스), substitutes.csv(대체 표현),
│   │                       lexicon.csv(용어사전), demo_sentences.json(안내 문장), fingerspell.json, order_stats.json(어순 통계, 참고용)
│   ├── eval/               평가 질문 (문서 안·밖, 안전 문장)과 CI 기준 점수 baseline.json
│   └── sample/             예시용 가짜 데이터
├── scripts/
│   ├── run/                Mac 실행 파일
│   ├── data/               인덱스·쪽 이미지·손 모양 만들기, 동작 가져오기 (팀원 3D·AI Hub·SignBridge)
│   ├── eval/               CI 평가, 검색·거절 평가, 수어 단어 점검, 화면 4종 점검, 배포 사이트 평가
│   ├── deploy/             Hugging Face 업로드 폴더 만들기, Space 깨우기, 아바타 용량 줄이기
│   ├── avatar/             Rocketbox → GLB 변환 (Blender)
│   └── model/              번역 모델 학습·평가
├── tests/                  pytest (CI 에서 자동)
├── deploy/huggingface/     Space 용 Dockerfile·설명
├── docs/                   문서 (아래 목록)
└── .github/workflows/      테스트, 배포(평가 통과 시), Space 깨우기, 주간 배포 사이트 평가
```

## 문서

| 문서 | 내용 |
|---|---|
| [docs/api.md](docs/api.md) | 엔드포인트, 응답 형식, 동작 좌표 규칙 |
| [docs/sign_pipeline.md](docs/sign_pipeline.md) | 답변을 수어 블록으로: 수어 문장, 블록 점검, 어순·표정, 숫자·시간, 대체 표현, 쉼 |
| [docs/avatar.md](docs/avatar.md) | 3D 아바타: 모델, 팔·손목·손가락, 표정, 렌더링 |
| [docs/motion_data.md](docs/motion_data.md) | 동작 데이터 형식과 가져오기 (팀원 자료, AI Hub, SignBridge) |
| [docs/retrieval.md](docs/retrieval.md) | 문서 검색 설계와 성능 |
| [docs/hallucination_guard.md](docs/hallucination_guard.md) | 환각 방지: 근거가 없으면 답하지 않기 |
| [docs/evaluation.md](docs/evaluation.md) | 테스트·CI 평가·점검 도구 |
| [docs/deploy_huggingface.md](docs/deploy_huggingface.md) | Hugging Face 배포와 운영 |
| [docs/translation_model.md](docs/translation_model.md) | 한국어→글로스 번역 모델 (설계 근거, 학습) |
| [docs/sign_input.md](docs/sign_input.md) | 카메라 수어 입력: 흐름, 개선 내용, 평가 |
| [docs/sos.md](docs/sos.md) | SOS: 응급실 고르는 기준, 쓰는 공공데이터, 개인정보 |
| [docs/offline.md](docs/offline.md) | 첫 로딩과 오프라인: 무엇을 언제 받고 저장하는지 |
| docs/project_plan.hwp | 과제 수행계획서 |
| docs/reports/missing_words_priority.csv | 동작 추가 우선순위 (팀 수집 요청용) |

## 테스트

```bash
pip install pytest httpx && python -m pytest -q tests     # 약 1분 20초, 번역 모델(torch) 필요 없음
python scripts/eval/ci_eval.py                           # 환각 방지 평가 (기준보다 떨어지면 배포 안 됨)
```
