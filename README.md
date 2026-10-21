# 이음 — 청각장애 근로자를 위한 산업안전 수어 안내

모델 파트 (모델 분석 및 구현)

과제 수행계획서의 **요소기술 1(RAG 검색)** + **② 한국어→한국수어 글로스 변환**을 구현한 코드.
결과물은 3D 아바타 팀이 바로 쓸 수 있는 `글로스 + 동작 시간 타임라인` JSON 으로 나온다.

```
질문 ─▶ KOSHA 문서 검색(RAG, 상위 3개) ─▶ 쉬운 문장 1~2개 ─▶ 전문용어 처리 ─▶ Transformer ─▶ 글로스 타임라인 ─▶ (아바타)
                      └─ 출처(문서·페이지) 함께 반환
```

## 만든 계기

**자막이 있어도, 더 익숙한 언어는 따로 있다.**

- 우리는 영어를 오래 배웠지만, 자막 없이 영어 영화를 보면 따라가기 어렵다. 태어날 때부터 청각장애가 있는 사람(농인)에게 한국어 글이 바로 그런 느낌이다. 이들에게 한국어는 제2언어에 가깝다. 그래서 글을 읽고 정보를 얻는 과정이 익숙하지 않고 느리다.
- 뉴스에 자막이 있는데도 현장에 수어 통역사가 서고, 화면 구석에 수어 화면을 따로 띄우는 것도 이 때문이다.
- 2026년 2월 10일 국무회의에서 「방송통신발전 기본법」 일부개정법률 공포안이 의결됐다. 공포 6개월 뒤 시행된다.
  - 재난방송 주관방송사(KBS)는 장애인의 시청을 돕기 위해 한국수어를 이용해야 한다 (제40조의2 제4항 신설).
  - 그 밖의 지상파 텔레비전 방송사업자는 재난방송 등을 할 때 한국수어를 이용하도록 노력해야 한다 (제40조 제4항 신설).
  - 출처: 방송미디어통신위원회 보도자료 「청각장애인도 ‘한국수어’로 재난방송 시청」 (2026. 2. 10.)
- 하지만 작업 현장의 안전 정보(보호구 착용·관리 규정 등)는 여전히 글로 된 설명서 위주다.

그래서 **글로 된 설명서 대신, 청각장애 근로자에게 익숙한 수어로 안전 정보를 전하는 서비스**를 만들기로 했다.

이음은 이렇게 동작한다.

1. 질문을 받으면 KOSHA 「개인보호구의 사용 및 관리에 관한 기술지원규정」에서 답변 근거를 찾는다.
2. 근거로 답변을 만든다. 미리 정리한 모범 답안을 쓰거나 Claude로 생성하고, 근거가 부족하면 답하지 않는다.
3. 답변에서 수어 단어를 뽑아, 손동작·표정·고개 움직임 데이터(조원 자료, AI Hub)로 3D 아바타가 수어를 표현한다.
4. 답변의 근거가 된 규정 페이지를 함께 보여준다.

## 폴더 구조

```
이음/
├── api.py                    서버 (FastAPI): /query, /ask, /translate, /motion, /eval …
├── 이음 실행.command          Mac 에서 더블클릭 → 서버 켜고 브라우저 열기
├── 이음 공개 실행.command     위 + 임시 공개 주소(Cloudflare)
├── requirements.txt
├── ksl/                      핵심 코드
│   ├── rag.py                문서 검색·답변(추출식 / Claude)
│   ├── pipeline.py           질문 → 답변 → 수어 단어 → 동작 (전체 연결)
│   ├── motion.py             수어 동작 형식·이어붙이기·2D→3D 깊이 추정
│   ├── translate.py, model.py, tokenizer.py, oov.py, data.py, config.py   한국어→수어 글로스 번역 모델
│   ├── pageimage.py, tables.py   출처를 원본 PDF 모양으로
│   └── evaluation.py, metrics.py 평가
├── web/                      화면
│   ├── index.html            웹 화면
│   ├── avatar.js             3D 수어 아바타 (Three.js + VRM)
│   ├── models/avatar.vrm     아바타 모델 (VRoid 샘플, 수정·재배포 허용)
│   ├── pages/                출처용 PDF 쪽 이미지
│   └── vendor/               라이브러리 (인터넷 없이 시연 가능)
├── data/
│   ├── kosha/                KOSHA 문서(PDF·쪽 배치)와 안내 문장 답변·예시 질문·핵심어
│   ├── sign/                 수어: motions/(단어별 3D 동작), sign_words.csv(말→수어 단어),
│   │                         lexicon.csv(용어사전), demo_sentences.json(조원 안내 문장)
│   ├── eval/                 평가용 질문 (문서 안·밖, 안전 문장)
│   ├── sample/               예시용 가짜 데이터
│   └── raw/                  (GitHub 에 안 올림) AI Hub·조원 원본 키포인트
├── scripts/
│   ├── data/                 자료 만들기: 검색 인덱스, PDF 쪽 이미지, 조원 키포인트 가져오기 등
│   ├── model/                번역 모델 학습·평가
│   ├── eval/                 환각 방지·검색 평가
│   └── deploy/               Hugging Face 배포 폴더 만들기, 아바타 용량 줄이기
├── deploy/huggingface/       Space 용 Dockerfile·설명
├── docs/                     API 명세, 환각 방지 정리(발표용), 배포 안내, 수행계획서
└── .github/workflows/        main 에 올리면 Hugging Face 에 자동 배포
```

## 실행 순서 (팀 데이터 기준)

```bash
pip install -r requirements.txt

# 0) 전처리 JSONL 키 확인 → ksl/config.py 의 FIELDS 오른쪽 값을 실제 키로 수정
python scripts/data/inspect_jsonl.py data/processed/ksl_safety.jsonl

# 1) 번역 모델 학습 (GPU 있으면 자동 사용, 결과: checkpoints/run1/best.pt + train/val/test.jsonl)
python scripts/model/train.py --data data/processed/ksl_safety.jsonl --out checkpoints/run1

# 2) 테스트셋 평가 (Transformer vs 사전 치환 베이스라인, 오류 목록 errors.csv)
python scripts/model/evaluate.py --run checkpoints/run1 --lexicon data/sign/lexicon.csv

# 3) KOSHA 인덱스 + 검색 평가 (목표 Recall@3 ≥ 0.90)
python scripts/data/build_index.py --docs data/kosha/A-G-12-2026.pdf --out checkpoints/kosha_index.pkl
python scripts/eval/eval_retrieval.py --index checkpoints/kosha_index.pkl --questions data/eval/eval_questions.csv

# 4) 동작사전 (둘 중 가능한 것)
python scripts/data/build_motion_dict.py --data data/processed/ksl_safety.jsonl --kp_root <AI Hub 키포인트 폴더> --out data/sign/motions
python scripts/data/extract_keypoints.py --video_dir <수어사전 영상 폴더> --out data/sign/motions   # pip install mediapipe opencv-python

# 5) 서버 + 웹 화면  →  브라우저 http://localhost:8000
KSL_CKPT=checkpoints/run1/best.pt KSL_INDEX=checkpoints/kosha_index.pkl KSL_MOTION=data/sign/motions uvicorn api:app --port 8000
```

### 조원 키포인트(문장별_수어키포인트) 반영
```bash
python scripts/data/import_team_keypoints.py ~/Downloads/문장별_수어키포인트 --out data/sign/motions
KSL_CKPT=checkpoints/test/best.pt KSL_INDEX=checkpoints/sample_index.pkl uvicorn api:app --port 8000
```
- 11개 안내 문장은 **번역 메모리**: 입력이 이 문장과 같으면 모델 대신 조원이 확정한 글로스를 사용 (`method: "memory"`)
- 질문 모드에서 질문이 안내 문장과 충분히 비슷하면 그 문장으로 답함 (`answer_mode: "demo"`)
- 같은 글로스는 문장이 달라도 동작을 재사용 → 동작사전에 있는 글로스끼리는 새 문장도 이어붙일 수 있음

### AI Hub 단어 동작 추가 (재난 안전 수어영상 VL 형태소)
```bash
# 형태소 JSON 에서 단어 구간을 잘라낸 후보(<글로스>__n.json.gz)를 data/raw/aihub_sel3 에 두고
python scripts/data/import_aihub_clips.py data/raw/aihub_sel3 --skip_existing --out data/sign/motions
```
- 글로스마다 후보 중 3D 이면서 손이 잘 잡히고 키포인트가 튀지 않는 것 1개를 골라 저장 (조원 동작은 그대로)
- 말 → 글로스 연결은 `data/sign/sign_words.csv` 에서 (예: 교체·바꾸 → 바꾸다1, 분진 → 먼지1#)
- 조원 추가 안내 문장 5개(07~11: 보안경·안전화·귀마개·안전대·호흡용 보호구)는 조원 키포인트 대부분이 1프레임짜리라,
  같은 뜻의 글로스를 AI Hub VL 에서 잘라 씀 (쓰러지다0·발0·흩날리다0·일어나다0·나쁘다1·안전벨트0 추가, 용품→물건1, 운동화→신발1, 상부→높다1 등).
  소음·귀마개·보안경처럼 맞는 수어가 없는 단어는 빠져 있어 `complete: false` — 수어 의미 검수 필요

### 샘플로 웹 화면까지 바로 보기
```bash
python scripts/data/make_sample_data.py && python scripts/data/make_sample_motion.py
python scripts/model/train.py --data data/sample/sample_ksl.jsonl --out checkpoints/test --epochs 40 --d_model 128 --layers 2 --batch_size 32
python scripts/data/build_index.py --docs data/sample/sample_safety_doc.txt --out checkpoints/sample_index.pkl --max_chars 150
KSL_CKPT=checkpoints/test/best.pt KSL_INDEX=checkpoints/sample_index.pkl KSL_MOTION=data/sample/motions uvicorn api:app --port 8000
```
샘플 동작(`make_sample_motion.py`)은 손이 원을 그리는 **가짜 동작**이라 화면 연결 확인용으로만 쓸 것.

- 글로스 필드는 `"작업장 안전모 착용"`(문자열), `["작업장", ...]`(리스트), `[{"gloss":..,"start":..,"end":..}]`(시간 포함) 세 형식 다 읽음.
- PDF 글자 추출이 깨지면, 팀이 정리한 페이지별 텍스트를 `=== page 3 ===` 줄로 구분한 `.txt` 로 넣으면 됨 (페이지 번호 그대로 출처에 표시).
- `ANTHROPIC_API_KEY` 를 설정하면 검색 결과를 LLM이 **문서 근거 안에서만** 짧은 쉬운 문장으로 정리. 없으면 검색된 문장을 그대로 뽑아 씀(추출식, 환각 없음).
- 시간 여유 있으면 `pip install sentence-transformers` 후 `--retriever dense` 로 임베딩 검색과 비교.

## API 응답 예시 (`POST /ask`)

```json
{
  "answer_text": "물체가 흩날릴 위험이 있는 작업에서는 보안경을 착용합니다.",
  "sentences": [{
    "gloss": ["물체", "날다", "위험", "있다", "작업", "보안경", "사용"],
    "timeline": [{"gloss": "물체", "start": 0.0, "duration": 0.78}, ...],
    "lexicon_terms": [], "unverified_terms": [], "dropped_terms": []
  }],
  "sources": [{"doc": "A-G-12-2026", "page": 2, "score": 0.29, "text": "..."}]
}
```
`timeline` 이 아바타 재생 순서·길이. `FS:용어` 는 수어 표현이 아직 없는 용어 → 아바타는 지문자로 처리.

---

## 모델 분석 — 설계 근거

**1. 왜 글로스 단위 토큰화인가**
SSL 벤치마크(Kim et al., 2024)에서 수어 글로스 단위 토큰화가 음성언어식·통계식 토큰화보다 성능이 좋았음. 출력 글로스를 쪼개지 않으면 아바타 동작 단위(글로스 1개 = 동작 1개)와도 바로 대응됨.
입력 한국어는 글자 단위 → 입력 쪽 미등록어가 없고, 조사·어미 처리를 모델이 학습.

**2. 전문용어(OOV) 처리 — 용어 슬롯 방식**
Kim et al.(2022, Avatar Layering)이 고유명사 변환으로 OOV를 보완해 BLEU-4 +8.77 을 얻은 방식을 단순화:
- 학습 때 문장과 글로스에 같이 나오는 단어를 30% 확률로 `<T0>` 같은 슬롯으로 바꿔 "슬롯은 그대로 옮긴다"를 학습
- 추론 때 `lexicon.csv` 용어 중 **모델이 모르는 것만** 슬롯으로 가리고, 번역 후 사전 글로스로 복원
- 사전에 수어가 없는 `추가검토` 용어는 지문자(`FS:`)로 출력 + `unverified_terms` 로 보고 → 모델이 비슷한 다른 단어를 지어내는 것 방지
- `보호` 가 `보호구` 안에서 잘못 잡히지 않도록 어절 시작 + 조사/어미만 허용

**3. 비교군 (발표용 근거)**
`evaluate.py` 가 같은 테스트셋에서 자동 비교:
- 사전 치환 베이스라인: 조사를 떼고 한국어 어순 그대로 글로스로 치환 (계획서의 "형태소 단위 치환" 방식)
- Transformer (사전 사용 / 미사용)
- AI Hub 기준 BLEU 0.1633 (sacrebleu 값 ÷100 으로 비교)

추가 진단: 길이 구간별 BLEU, 테스트 OOV 글로스 비율, 자주 빠뜨리는 글로스 Top 15, 틀린 문장 목록.
BLEU 는 낮은데 글로스 F1 이 높으면 → "어휘는 맞고 어순이 틀림" 으로 해석.

**4. 검색 (RAG)**
기본은 글자 2~4gram TF-IDF: 설치·인터넷이 필요 없고 "착용해야/착용합니다" 같은 활용 차이에 강함. 문장 경계를 지키며 chunk 를 만들고 앞 chunk 의 마지막 문장을 겹쳐 문맥이 잘리지 않게 함.

## 샘플 데이터로 동작 확인한 결과 (⚠ 실제 성능 아님)

`scripts/data/make_sample_data.py` 로 만든 **템플릿 가짜 데이터 441문장**(실제 AI Hub 데이터 아님)으로 끝까지 돌려 확인함. CPU 1분 학습.

| | BLEU | 정확일치 | 글로스 F1 |
|---|---|---|---|
| Transformer | 76.25 | 0.533 | 0.891 |
| 사전 치환 베이스라인 | 13.81 | 0.000 | 0.834 |

- 학습에 없던 `작업복` → 사전 슬롯으로 정상 번역: `작업장에서는 작업복을 반드시 착용해야 합니다.` → `작업장 작업복 착용 반드시 필요`
- 사전에 수어가 없는 `방진마스크` → `FS:방진마스크` 로 출력되고 검토 대상으로 표시 (슬롯 처리 전엔 `귀마개` 로 잘못 번역됐음)
- 샘플 문서 검색 Recall@3 = 8/8

템플릿 데이터라 수치가 높게 나온 것이므로 **발표 수치는 반드시 팀 JSONL로 다시 낼 것.**


## KOSHA 검색 성능 (실제 A-G-12-2026, 25쪽 → 74개 chunk)

| 질문 세트 | Recall@1 | Recall@3 | Recall@5 |
|---|---|---|---|
| 개발용 30문항 `data/eval/eval_questions.csv` | 0.533 | **0.967** | 0.967 |
| 새로 쓴 10문항 `data/eval/heldout_questions.csv` | 0.200 | 0.600 | 0.800 |

- 개발용 질문은 현장 말→지침 용어 확장 규칙(`QUERY_SYNONYMS`)을 만들 때 참고했기 때문에 점수가 높게 나옴. **발표 때는 팀원이 새로 쓴 질문으로 다시 재는 게 공정함**
- 확장 규칙 전 개발용 Recall@3: 0.767 → 확장 후 0.967
- PDF는 pypdf `layout` 모드로 추출해야 한글 띄어쓰기가 살아남 (기본 모드는 띄어쓰기가 다 사라짐)
- 더 올리려면: 임베딩 검색(`--retriever dense`, 인터넷 되는 맥에서 `pip install sentence-transformers`)과 비교, 또는 LLM 키를 넣어 답변을 쉬운 문장으로 정리

## 환각 방지 — 근거 없으면 답하지 않기 (자세한 건 docs/환각방지.md)

| | 조치 전 | 조치 후 |
|---|---|---|
| 문서 밖 입력 28개(인사·잡담·문서에 없는 주제) 거절 | 0 | **28** |
| 문서 안 질문 40개에 답함 | 40 | 38 |
| 안전 안내 문장 10개 번역 | 10 | 10 |

검색 1위 점수 < 0.11 이면 답 거절, 산업안전 핵심어가 없는 문장은 번역 거절. 재현: `python scripts/eval/eval_abstain.py`

기준값 0.11 확인 (`python scripts/eval/sweep_threshold.py`): 문서 안 40개 답함 + 문서 밖 28개 거절의 합이 0.105~0.115 에서 66/68 로 가장 높고, 0.11 은 그 가운데.
문서 밖 질문 최고 점수(소화기 사용법 0.105)와 문서 안 질문(폭염 보호구 0.117) 사이가 좁아서 기준값을 더 옮겨도 나아지지 않음 — 놓친 2개(안전대 종류, 배달 오토바이 헬멧)는 기준값이 아니라 검색 문제.

## 동작 데이터 형식 (요약, 자세한 건 docs/API.md)
- 프레임 = `body` 8점 + 오른손/왼손 21점, 목=(0,0) · 어깨너비=1 로 정규화 → 다른 영상 클립끼리 크기·위치가 맞음
- 글로스 클립 사이 0.2초 보간, 동작 없는 글로스(`FS:` 지문자 등)는 기본 자세 + 화면에 글자 표시
- AI Hub 키포인트(OpenPose JSON, 2D/3D, people 이 list/dict 어느 쪽이든)와 MediaPipe 결과를 같은 형식으로 변환
- 동작사전 만들 때 같은 글로스 클립이 여러 개면 길이가 중앙값에 가장 가까운 1개 사용

## 남은 일 / 확장
- 팀 JSONL 로 학습 → 실제 BLEU, 베이스라인 비교표
- KOSHA 실제 질문 20~30개로 `rag_questions.csv` 작성 → Recall@3
- `lexicon.csv` 에 한국수어사전 조사 결과 계속 추가 (gloss 칸 비우면 추가검토로 처리)
- 데이터가 충분하면 `--d_model 512 --layers 6`, 적으면 기본값(256, 3층) 유지
- 비수지 표현(NMS)은 현재 그대로 통과만 함 → 후반에 출력 채널 추가 + SignBLEU 검토
- 아바타 표정·입모양(비수지) 반영: 키포인트의 face 70점 → VRM 표정
- 3·4·5번 문장 부족 단어(안전모, 착용, 보호구 등) 키포인트 보강
- 실제 영상으로 `extract_keypoints.py` 는 아직 못 돌려봄 (MediaPipe 모델 다운로드가 이 작업 환경에서 막혀 있었음) → 사전 영상 1개로 먼저 확인
