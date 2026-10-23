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
│   ├── order.py              수어 어순(부정·필요·가능·시간) + 문법 표정
│   ├── motion.py             수어 동작 형식·이어붙이기·2D→3D 깊이 추정
│   ├── translate.py, model.py, tokenizer.py, oov.py, data.py, config.py   한국어→수어 글로스 번역 모델
│   ├── pageimage.py, tables.py   출처를 원본 PDF 모양으로
│   └── evaluation.py, metrics.py 평가
├── web/                      화면
│   ├── index.html            웹 화면
│   ├── avatar.js             3D 수어 아바타 (Three.js): 몸통·팔·손가락·고개·표정
│   ├── rig.js, face.js       GLB 뼈 이름 자동 인식 / ARKit 표정·한국어 입 모양
│   ├── avatar-vrm.js         예전 VRM 아바타 꾸미기 (?avatar=vrm)
│   ├── handshapes.json       자주 나오는 손 모양 (scripts/data/build_handshapes.py)
│   ├── models/avatar.glb     반실사 아바타 (대역: MakeHuman/MPFB CC0, TalkingHead 저장소)
│   ├── models/avatar.vrm     예전 아바타 (VRoid 샘플, 수정·재배포 허용)
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
- `ANTHROPIC_API_KEY` 를 설정하면 (Anthropic SDK, 모델은 `LLM_MODEL` 로 바꿀 수 있음) 검색 결과를 LLM이 **문서 근거 안에서만** 짧은 쉬운 문장으로 정리. 없으면 검색된 문장을 그대로 뽑아 씀(추출식, 환각 없음).
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


## KOSHA 검색 성능 (실제 A-G-12-2026, 25쪽 → 77개 chunk)

| 질문 세트 | Recall@1 | Recall@3 | Recall@5 |
|---|---|---|---|
| 개발용 30문항 `data/eval/eval_questions.csv` | 0.733 → 0.767 | 1.000 → **1.000** | 1.000 → 1.000 |
| 새로 쓴 10문항 `data/eval/heldout_questions.csv` | 0.300 → 0.600 | 0.700 → **1.000** | 0.700 → 1.000 |
| 새로 쓴 20문항 `data/eval/heldout2_questions.csv` | 0.600 → 0.800 | 0.750 → **0.950** | 0.850 → 0.950 |
| 새로 쓴 22문항 `data/eval/heldout3_questions.csv` (개선 전에 작성) | 0.455 → 0.591 | 0.727 → **0.773** | 0.727 → 0.773 |

(왼쪽: 글자 TF-IDF + 단어 BM25 / 오른쪽: + 절 제목 + 이웃 chunk + 현장 말 확장 추가 + 합성어 줄여 찾기. 2026-10 같은 환경에서 측정)

검색 순위 = 글자 TF-IDF + `HYBRID_W`(1.2) × 단어 BM25 + `NEIGHBOR_W`(0.15) × 같은 쪽 앞·뒤 chunk 점수 (`ksl/rag.py`)
- 단어 BM25: kiwipiepy 로 조사·어미를 뗀 명사·동사만 비교
- **절 제목**: chunk 마다 속한 절 제목('7. 개인보호구의 종류 7.2 안전화의 종류')을 BM25 에 함께 넣음 → 표 칸만 있는 chunk 도 "방진마스크 등급", "안전화 종류" 로 찾음.
  표 안의 번호 목록('7.녹인 금속을…')은 번호가 차례대로가 아니거나 '장소·업무'로 끝나면 제목으로 보지 않음
- **이웃 chunk**: 표가 chunk 여러 개로 잘리면 '용광로'(작업 칸)와 '방열복'(보호구 칸)이 다른 chunk 에 들어가서, 같은 쪽 바로 옆 chunk 가 맞으면 조금 올려 줌
- **합성어 줄여 찾기**: 문서에 없는 단어는 앞부분으로 ('잠수부' → '잠수')
- 현장 말 확장(`QUERY_SYNONYMS`)에 용광로→고열작업·방열복, 차가운→저온물체, 작업화→안전화, 오토바이 헬멧→이륜자동차 승차용 안전모 등 추가
- 비율과 확장 규칙은 개발용·heldout·heldout2 실패만 보고 정함. heldout3 은 개선 전에 만든 비교용
  (합성어 줄여 찾기는 heldout3 에서 '잠수부'가 떨어진 것을 보고 넣어서 heldout3 도 완전히 처음 보는 질문은 아님)
- 남은 실패(heldout3): 물 많은 곳(→내수성 고무제안전화), 압력 높은 곳(→고압작업), 용접 불빛(→용접용 눈보호구), 작은 보호구 보관(→상자), 금속 가루(→금속류)
  → 현장 말과 규정 용어가 다른 경우라 임베딩 검색(`--retriever dense`)이나 Claude 로 질문을 규정 용어로 바꾸는 방법이 다음 단계
  (이 작업 환경에서는 Hugging Face 접속이 막혀 임베딩 모델을 받지 못해 비교하지 못함)
- 인덱스 형식이 바뀜(`INDEX_VERSION = 2`) → 예전 `kosha_index.pkl` 은 서버가 켜질 때 자동으로 다시 만듦. 직접 만들 때는
  `python scripts/data/build_index.py --docs data/kosha/A-G-12-2026.pdf --out checkpoints/kosha_index.pkl --max_chars 300`
- PDF는 pypdf `layout` 모드로 추출해야 한글 띄어쓰기가 살아남 (기본 모드는 띄어쓰기가 다 사라짐)

## 환각 방지 — 근거 없으면 답하지 않기 (자세한 건 docs/환각방지.md)

| | 조치 전 | 조치 후 |
|---|---|---|
| 문서 밖 입력 28개(인사·잡담·문서에 없는 주제) 거절 | 0 | **28** |
| 문서 안 질문 40개에 답함 | 40 | 39 |
| 안전 안내 문장 10개 번역 | 10 | 10 |

검색 상위 3개 중 가장 높은 글자 TF-IDF 점수 < 0.11 이면 답 거절, 산업안전 핵심어가 없는 문장은 번역 거절. 재현: `python scripts/eval/eval_abstain.py`
(단어 BM25 가 1위를 바꿔도 상위 안에 질문과 글자가 많이 겹치는 문단이 있으면 근거가 있다고 봄. 1위 점수만 보면 검색 개선 뒤 36/40 으로 떨어졌음)

기준값 0.11 확인 (`python scripts/eval/sweep_threshold.py`): 문서 안 40개 답함 + 문서 밖 28개 거절의 합이 0.11 에서 67/68 로 가장 높음.
놓친 1개(안전대 종류)는 기준값이 아니라 검색 문제.

## 수어 어순과 문법 표정 (`ksl/order.py`)

답변 문장에서 뽑은 수어 단어를 한국수어 어순으로 바꾸고, 어미로 나타나던 문법을 수어 단어와 표정으로 꺼냄.
한국수어도 '주어-목적어-동사' 순서라 단어 순서는 대부분 그대로 두고, 아래만 바꿈 (조원 확정 문장·샘플 글로스의 순서를 따름).

| 한국어 | 수어 단어 | 표정·고개 |
|---|---|---|
| 보호구를 착용하지 **않으면** 다칠 **수 있습니다** | 보호 입다 **안하다** / 부상 **가능** | 조건절 눈썹 올림, 부정은 동사 위에서 고개 젓기 |
| 작업할 때 안전모를 **반드시** 착용**해야** 한다 | 일하다 때 / 안전모 입다 **꼭 필요** | 조건절 눈썹 올림, '꼭'·'필요'에서 끄덕 |
| 정화통은 **6개월마다** 교체해야 합니다 | **6개월** 정화통 바꾸다 필요 | 시간 표현은 절 맨 앞 |
| 보호구를 벗지 **마세요** / 착용을 해서는 **안 됩니다** | 벗다 **금지** / 입다 **금지** | 고개 젓기 + 단호한 얼굴 |
| 착용하지 **않으면 안 됩니다** (이중 부정) | 입다 **필요** | 끄덕 |

- 절은 연결어미(-면, -고, -어서)와 관형형(-ㄹ 때, -ㄴ 근로자)에서 나눔. '-지 않', '-어야 하' 처럼 보조용언이 붙으면 같은 절
- '안하다·금지·못하다'는 아직 동작 데이터가 없어 회색 단어로 보이고, 대신 바로 앞 동사(동작 있음)를 하는 동안 고개를 저음 (수어에서도 부정 고개 젓기는 동사와 함께 함)
- 문법 표정은 `sentences[].nms`·`motion.segments[].nms` 로 내려가고, `web/avatar.js` 가 단어 뜻 표정 표(`NMS`)와 합쳐 씀 (고개 움직임은 문법 쪽 우선)
- 조원 확정 안내 문장(번역 메모리)은 확정 글로스 그대로

## 3D 아바타 (`web/avatar.js`)

기본 아바타는 **반실사 GLB** (`web/models/avatar.glb`). 예전 애니메이션풍 VRM 은 주소 뒤에 `?avatar=vrm` 을 붙이면 비교용으로 볼 수 있음.
지금 GLB 는 대역 모델: [TalkingHead](https://github.com/met4citizen/TalkingHead) 저장소의 `mpfb.glb` (MakeHuman/MPFB, **CC0**) 를 37MB → 8.5MB 로 줄인 것.
MakeHuman 으로 30대 한국인 남성 작업자 모델을 따로 만드는 중 (완성되면 `web/models/avatar.glb` 교체, 다른 이름은 `?avatar=파일이름`).

| 부분 | 하는 일 |
|---|---|
| 모델 연결 (`web/rig.js`) | CC4(`CC_Base_*`)·Mixamo·MPFB·Ready Player Me 뼈 이름 자동 인식 → VRM 과 같은 정규화 뼈대. 이름이 다르면 `web/models/avatar.json` 의 `bones` 로 지정. cm 단위·뒤를 보는 모델도 자동 보정 |
| 렌더링 | ACES 톤 매핑 + 실내 환경광 + 3점 조명(주광·보조광·윤곽광), 피부는 덜 번들거리게, 옷은 짙게(손이 잘 보이게), 옷이 몸 위에 그려지게 |
| 몸통·쇄골 | 데이터 어깨선의 기울기·회전으로 척추·가슴을 나눠 돌림, 손을 앞으로 뻗으면 살짝 숙임, 숨쉬기. 손을 어깨 위로 들면 쇄골을 들고, 앞으로 뻗거나 몸 반대쪽으로 넘기면 내밈 |
| 팔 | 팔꿈치 방향 = 데이터 70% + 기본(아래·바깥·뒤) 30%, 위로 들리지 않게, 시간에 따라 부드럽게. 팔꿈치가 몸통 안으로 들어가면 바깥으로 돌려 다시 계산. 손목 비틀림의 45% 는 아래팔에 (CC4 비틀림 뼈가 있으면 그 뼈에) → 손목이 꼬여 보이지 않음 |
| 손가락 | 굽힘은 데이터의 **마디 사이 각도**로 정함(손바닥 방향 추정이 틀어져도 갈고리처럼 꺾이지 않음, 편 상태 기준은 `handshapes.json` 의 base). 손목 꺾임은 60° 까지(손·팔 데이터의 손목이 어긋나면 40°), 엄지 포함 관절 범위 제한, 끝마디는 가운데 마디의 2/3 쯤으로 연동, 옆 손가락과 겹치지 않게 벌림 제한, 자주 나오는 손 모양(`web/handshapes.json`, 동작 데이터 24개 군집) 쪽으로 최대 35% 끌어당김, 손이 잠깐 안 잡히면 0.5초 동안 마지막 손 모양 유지 |
| 단어 사이 | 서버가 연결 구간을 표시(`tr`) → 앞·뒤 자세의 뼈 회전을 최소 저크 곡선으로 섞음. 손이 멀리 움직일수록 연결이 길고, 단어 끝엔 짧게 멈춤 |
| 부드러움 | 서버: 관절 좌표에 One Euro 필터 (느릴 땐 떨림 제거, 빠를 땐 늦지 않게). 아바타: 뼈마다 따라가는 빠르기(손가락 0.03초 ~ 몸통 0.12초)를 프레임 간 시간으로 계산 → 화면 주사율과 상관없이 같은 속도 |
| 고개 | 데이터 각도 + 단어·문법 움직임(끄덕·젓기·갸웃)을 스프링으로(살짝 넘쳤다 돌아옴), 목 40%·머리 60% |
| 얼굴 (`web/face.js`) | ARKit 52 표정 이름으로 통일(VRM 은 비슷한 기본 표정으로 바꿔 씀). 문법 표정은 1.25배 과장. 단어의 한국어를 입 모양으로(모음 → 입 모양, ㅁ·ㅂ·ㅍ 는 입술 닫힘). 시선은 보는 사람 쪽 + 손이 얼굴 앞에 오면 살짝 손 쪽, 미세한 눈 움직임, 고개를 크게 돌린 뒤 깜빡임 |
| 안전모 | 머리 뼈를 따르는 꼭짓점으로 머리통 크기를 재서 이마를 덮게 씌움 |

- 모델을 바꿀 때: GLB(손가락 뼈 포함, ARKit 표정 셰이프키 권장)를 `node scripts/deploy/slim_glb.mjs 원본.glb web/models/avatar.glb 2048` 로 줄여서 넣기
  (모프 법선·눈동자 모프 제거, 텍스처 WebP 2048. `npm i @gltf-transform/core @gltf-transform/extensions @gltf-transform/functions sharp` 필요)
- 손 모양 다시 만들기: `python scripts/data/build_handshapes.py` (동작 데이터가 바뀌었을 때)
- 남은 일: 영상에서 관절 점 대신 뼈 회전을 바로 추정하는 모델(손 HaMeR, 몸 SMPLer-X 계열)로 동작사전 다시 만들기 — GPU 와 원본 영상 필요

## 테스트 (`tests/`, GitHub Actions `.github/workflows/test.yml`)

```bash
pip install pytest httpx && python -m pytest -q tests     # 약 15초, 번역 모델(torch) 필요 없음
```
- 검색 Recall@3 기준 (개발용 ≥0.95, heldout ≥0.9, heldout2 ≥0.85, heldout3 ≥0.7), 절 제목 인식
- 답변 거절: 문서 밖 28개 모두 거절, 문서 안 40개 중 38개 이상 답함
- 동작 이어붙이기(연결 구간 표시·멈춤·One Euro 필터), 손 모양 파일, 웹 JS 문법
- 수어 어순·문법 표정, API 응답 형식, Claude 답변 모드(실제 API 대신 가짜 응답: 거절이면 추출식으로 대체)
- 푸시·PR 마다 자동 실행

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
- 문법 표정(부정·조건·필요·강조)은 규칙으로 붙임 → 농인·통역사 검수, 의문문·높낮이 등 추가 + SignBLEU 검토
- '안하다·금지·못하다·아니다' 동작 데이터 확보 (지금은 표정·고개로만 표현)
- 3·4·5번 문장 부족 단어(안전모, 착용, 보호구 등) 키포인트 보강
- 실제 영상으로 `extract_keypoints.py` 는 아직 못 돌려봄 (MediaPipe 모델 다운로드가 이 작업 환경에서 막혀 있었음) → 사전 영상 1개로 먼저 확인
