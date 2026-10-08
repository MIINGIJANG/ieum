# 이음 — 청각장애 근로자를 위한 산업안전 수어 안내

모델 파트 (모델 분석 및 구현)

과제 수행계획서의 **요소기술 1(RAG 검색)** + **② 한국어→한국수어 글로스 변환**을 구현한 코드.
결과물은 3D 아바타 팀이 바로 쓸 수 있는 `글로스 + 동작 시간 타임라인` JSON 으로 나온다.

```
질문 ─▶ KOSHA 문서 검색(RAG, 상위 3개) ─▶ 쉬운 문장 1~2개 ─▶ 전문용어 처리 ─▶ Transformer ─▶ 글로스 타임라인 ─▶ (아바타)
                      └─ 출처(문서·페이지) 함께 반환
```

## 폴더 구조

| 파일 | 역할 |
|---|---|
| `ksl/config.py` | **팀 JSONL 키 이름 매핑(FIELDS)**, 하이퍼파라미터 |
| `ksl/data.py` | JSONL 로딩, 누락·시간오류·중복 제거, 문장 단위 train/val/test 분할, 글로스별 평균 동작시간 |
| `ksl/tokenizer.py` | 한국어=글자 단위 / 수어=**글로스 단위** 토큰화 |
| `ksl/model.py` | Transformer encoder-decoder (greedy / beam search) |
| `ksl/oov.py` | 전문용어(OOV) 슬롯 처리 + `lexicon.csv` 용어사전 |
| `ksl/translate.py` | 번역기, 사전 치환 베이스라인 |
| `ksl/metrics.py` | BLEU(sacrebleu), 정확일치율, 글로스 F1 |
| `ksl/rag.py` | KOSHA 문서 분할·검색(TF-IDF 기본 / 임베딩 선택)·답변 문장 생성 |
| `ksl/motion.py` | 수어 동작(관절 좌표) 공통 형식, AI Hub(OpenPose)·MediaPipe 변환, 동작사전, 글로스 이어붙이기 |
| `ksl/pipeline.py`, `api.py` | 전체 연결 + FastAPI (`/ask`, `/translate`, `/motion`) |
| `web/index.html`, `web/avatar.js` | 시연용 웹 화면 + **3D 수어 아바타**(Three.js + VRM, 팔·손목·손가락 관절). 3D가 안 되면 2D 그림으로 자동 대체 |
| `web/vendor/`, `web/models/avatar.vrm` | 라이브러리·3D 모델 내장 → 인터넷 없이도 시연 가능 (모델: pixiv 샘플, 재배포·상업 이용 허용) |
| `data/demo_sentences.json`, `data/motions/` | 조원이 정리한 5개 안내 문장(번역 메모리)과 글로스별 3D 동작 |
| `docs/API.md`, `docs/mock/` | 프론트/아바타 담당용 API 명세 + 실제 응답 예시 파일 |
| `scripts/` | 학습·평가·인덱스·검색평가·JSONL 구조 확인·샘플데이터 |
| `data/lexicon.csv` | 산업안전 용어 ↔ 수어 글로스 (한국수어사전 조사 결과 넣는 곳) |

## 실행 순서 (팀 데이터 기준)

```bash
pip install -r requirements.txt

# 0) 전처리 JSONL 키 확인 → ksl/config.py 의 FIELDS 오른쪽 값을 실제 키로 수정
python scripts/inspect_jsonl.py data/processed/ksl_safety.jsonl

# 1) 번역 모델 학습 (GPU 있으면 자동 사용, 결과: checkpoints/run1/best.pt + train/val/test.jsonl)
python scripts/train.py --data data/processed/ksl_safety.jsonl --out checkpoints/run1

# 2) 테스트셋 평가 (Transformer vs 사전 치환 베이스라인, 오류 목록 errors.csv)
python scripts/evaluate.py --run checkpoints/run1 --lexicon data/lexicon.csv

# 3) KOSHA 인덱스 + 검색 평가 (목표 Recall@3 ≥ 0.90)
python scripts/build_index.py --docs data/kosha/A-G-12-2026.pdf --out checkpoints/kosha_index.pkl
python scripts/eval_retrieval.py --index checkpoints/kosha_index.pkl --questions data/rag_questions.csv

# 4) 동작사전 (둘 중 가능한 것)
python scripts/build_motion_dict.py --data data/processed/ksl_safety.jsonl --kp_root <AI Hub 키포인트 폴더> --out data/motions
python scripts/extract_keypoints.py --video_dir <수어사전 영상 폴더> --out data/motions   # pip install mediapipe opencv-python

# 5) 서버 + 웹 화면  →  브라우저 http://localhost:8000
KSL_CKPT=checkpoints/run1/best.pt KSL_INDEX=checkpoints/kosha_index.pkl KSL_MOTION=data/motions uvicorn api:app --port 8000
```

### 조원 키포인트(문장별_수어키포인트) 반영
```bash
python scripts/import_team_keypoints.py ~/Downloads/문장별_수어키포인트 --out data/motions
KSL_CKPT=checkpoints/test/best.pt KSL_INDEX=checkpoints/sample_index.pkl uvicorn api:app --port 8000
```
- 5개 안내 문장은 **번역 메모리**: 입력이 이 문장과 같으면 모델 대신 조원이 확정한 글로스를 사용 (`method: "memory"`)
- 질문 모드에서 질문이 안내 문장과 충분히 비슷하면 그 문장으로 답함 (`answer_mode: "demo"`)
- 같은 글로스는 문장이 달라도 동작을 재사용 → 동작사전에 있는 글로스끼리는 새 문장도 이어붙일 수 있음

### 샘플로 웹 화면까지 바로 보기
```bash
python scripts/make_sample_data.py && python scripts/make_sample_motion.py
python scripts/train.py --data data/sample/sample_ksl.jsonl --out checkpoints/test --epochs 40 --d_model 128 --layers 2 --batch_size 32
python scripts/build_index.py --docs data/sample/sample_safety_doc.txt --out checkpoints/sample_index.pkl --max_chars 150
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

`scripts/make_sample_data.py` 로 만든 **템플릿 가짜 데이터 441문장**(실제 AI Hub 데이터 아님)으로 끝까지 돌려 확인함. CPU 1분 학습.

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
| 개발용 30문항 `data/kosha/eval_questions.csv` | 0.533 | **0.967** | 0.967 |
| 새로 쓴 10문항 `data/kosha/heldout_questions.csv` | 0.200 | 0.600 | 0.800 |

- 개발용 질문은 현장 말→지침 용어 확장 규칙(`QUERY_SYNONYMS`)을 만들 때 참고했기 때문에 점수가 높게 나옴. **발표 때는 팀원이 새로 쓴 질문으로 다시 재는 게 공정함**
- 확장 규칙 전 개발용 Recall@3: 0.767 → 확장 후 0.967
- PDF는 pypdf `layout` 모드로 추출해야 한글 띄어쓰기가 살아남 (기본 모드는 띄어쓰기가 다 사라짐)
- 더 올리려면: 임베딩 검색(`--retriever dense`, 인터넷 되는 맥에서 `pip install sentence-transformers`)과 비교, 또는 LLM 키를 넣어 답변을 쉬운 문장으로 정리

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
