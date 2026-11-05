# 문서 검색 (`ksl/rag.py`)

KOSHA 「개인보호구의 사용 및 관리에 관한 기술지원규정」(A-G-12-2026, 25쪽)을 300자 안팎 문단(chunk) 77개로 나눠 검색한다.

## 나누기

- PDF 는 pypdf `layout` 모드로 추출 (기본 모드는 한글 띄어쓰기가 사라짐)
- 문장 경계를 지키며 나누고, 앞 문단의 마지막 문장을 겹쳐 문맥이 잘리지 않게 함
- 문단마다 속한 절 제목('7. 개인보호구의 종류 7.2 안전화의 종류')을 붙임 → 표 칸만 있는 문단도 "안전화 종류"로 찾음
- 인덱스 형식이 바뀌면(`INDEX_VERSION`) 서버가 켜질 때 다시 만듦. 직접 만들기:
  `python scripts/data/build_index.py --docs data/kosha/A-G-12-2026.pdf --out checkpoints/kosha_index.pkl --max_chars 300`

## 순위

```
점수 = 글자 2~4gram TF-IDF + 1.2 × 단어 BM25(절 제목 포함) + 0.15 × 같은 쪽 앞·뒤 문단 점수 중 큰 것
```

- 글자 TF-IDF: 설치·인터넷이 필요 없고 "착용해야/착용합니다" 같은 활용 차이에 강함
- 단어 BM25: kiwipiepy 로 조사·어미를 뗀 명사·동사만. 문서에 없는 합성어는 앞부분으로 ('잠수부' → '잠수')
- 이웃 문단: 표가 여러 문단으로 잘리면 '용광로'(작업 칸)와 '방열복'(보호구 칸)이 갈라지므로 옆 문단이 맞으면 조금 올림
- 현장 말 확장(`QUERY_SYNONYMS`): 용광로 → 고열작업·방열복, 작업화 → 안전화, 오토바이 헬멧 → 이륜자동차 승차용 안전모 등

## 성능

| 질문 세트 | Recall@1 | Recall@3 | Recall@5 |
|---|---|---|---|
| 개발용 30문항 `data/eval/eval_questions.csv` | 0.700 | **1.000** | 1.000 |
| heldout 10문항 | 0.600 | **1.000** | 1.000 |
| heldout2 20문항 | 0.800 | **0.950** | 0.950 |
| heldout3 22문항 (개선 전에 작성한 비교용) | 0.591 | **0.773** | 0.773 |

재현: `python scripts/eval/eval_retrieval.py --index checkpoints/kosha_index.pkl --questions data/eval/eval_questions.csv` (2026-11 측정)

- 가중치와 확장 규칙은 개발용·heldout·heldout2 실패만 보고 정함. heldout3 은 비교용이지만 '잠수부' 실패를 보고 합성어 줄여 찾기를 넣었으므로 완전히 처음 보는 질문은 아님
- 남은 실패(heldout3): 물 많은 곳(→내수성 고무제안전화), 압력 높은 곳(→고압작업), 용접 불빛(→용접용 눈보호구) 등 현장 말과 규정 용어가 다른 경우
- 시도했지만 넣지 않은 것: 상위 5~8개를 문장 단위 글자 유사도로 다시 순위 매기기 — 4개 세트 모두 Recall@1 이 오르지 않음
- 다음 단계 후보: 임베딩 검색(`--retriever dense`, sentence-transformers) 또는 순위 재정렬 전용 모델. 이 작업 환경에서는 Hugging Face 모델을 받을 수 없어 비교하지 못함

## 답변 (`SafetyRAG.answer`)

- `ANTHROPIC_API_KEY` 가 있으면 Claude 가 **검색된 문단 안에서만** 짧은 쉬운 문장으로 답함 (모델은 `LLM_MODEL`). 문서에 없으면 "문서에서 찾을 수 없습니다" → 답하지 않음 처리
- 없거나 실패하면 검색 문단에서 질문과 가장 맞는 문장을 그대로 뽑음 (추출식)
- 준비된 질문(`data/kosha/answers.json`)은 모범 답안을 씀 — 질문에 정해 둔 핵심어가 있어야 함
- 근거가 약하면 답하지 않는 기준은 `docs/hallucination_guard.md`
