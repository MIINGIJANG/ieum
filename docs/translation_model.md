# 한국어 → 수어 글로스 번역 모델

`ksl/translate.py`·`model.py`·`tokenizer.py`·`oov.py`·`data.py`·`config.py`·`metrics.py`.
서비스 답변의 수어 블록은 지금 **답변에 실제로 있는 말만** 단어표로 바꾸는 방식(`docs/sign_pipeline.md`)을 쓰고,
이 모델은 '문장 직접 번역'(`POST /translate`, 검색창에 질문이 아닌 문장을 넣었을 때)에서 쓴다.
학습이 덜 된 모델은 답변에 없는 단어를 지어낼 수 있어서(`docs/hallucination_guard.md` 1절) 답변 수어에는 쓰지 않는다.

## 학습·평가

```bash
python scripts/data/inspect_jsonl.py data/processed/ksl_safety.jsonl          # 키 확인 → ksl/config.py FIELDS 수정
python scripts/model/train.py --data data/processed/ksl_safety.jsonl --out checkpoints/run1
python scripts/model/evaluate.py --run checkpoints/run1 --lexicon data/sign/lexicon.csv   # 베이스라인 비교, errors.csv
```

- GPU 가 있으면 자동 사용. 결과: `checkpoints/run1/best.pt` + train/val/test.jsonl
- 서버: `KSL_CKPT=checkpoints/run1/best.pt uvicorn api:app` (모델은 처음 번역할 때 불러옴)
- 데이터가 충분하면 `--d_model 512 --layers 6`, 적으면 기본값(256, 3층)
- 글로스 필드는 문자열·리스트·시간 포함 리스트 세 형식 모두 읽음

## 설계 근거

1. **글로스 단위 토큰화** — 수어 벤치마크(Kim et al., 2024)에서 글로스 단위가 음성언어식·통계식 토큰화보다 좋았음. 글로스 1개 = 동작 1개로 바로 대응. 입력 한국어는 글자 단위라 입력 쪽 미등록어가 없음
2. **전문용어 슬롯** — Kim et al.(2022, Avatar Layering)의 고유명사 변환을 단순화: 학습 때 문장·글로스에 같이 나오는 단어를 30% 확률로 `<T0>` 슬롯으로 바꿔 "슬롯은 그대로 옮긴다"를 학습, 추론 때 `lexicon.csv` 용어 중 모델이 모르는 것만 슬롯으로 가리고 번역 뒤 사전 글로스로 복원. 수어가 없는 용어는 지문자(`FS:`) + `unverified_terms`
3. **비교군** — 같은 테스트셋에서 사전 치환 베이스라인(조사를 떼고 한국어 어순 그대로)과 Transformer(사전 사용/미사용). 길이별 BLEU, OOV 비율, 자주 빠뜨리는 글로스, 틀린 문장 목록. BLEU 는 낮은데 글로스 F1 이 높으면 "어휘는 맞고 어순이 틀림"

## 샘플 데이터 결과 (⚠ 실제 성능 아님)

`scripts/data/make_sample_data.py` 의 템플릿 가짜 데이터 441문장, CPU 1분 학습.

| | BLEU | 정확일치 | 글로스 F1 |
|---|---|---|---|
| Transformer | 76.25 | 0.533 | 0.891 |
| 사전 치환 베이스라인 | 13.81 | 0.000 | 0.834 |

템플릿 데이터라 높게 나온 값이다. 발표 수치는 팀 데이터로 다시 내야 한다.

샘플로 화면까지 보기:
```bash
python scripts/data/make_sample_data.py && python scripts/data/make_sample_motion.py
python scripts/model/train.py --data data/sample/sample_ksl.jsonl --out checkpoints/test --epochs 40 --d_model 128 --layers 2 --batch_size 32
python scripts/data/build_index.py --docs data/sample/sample_safety_doc.txt --out checkpoints/sample_index.pkl --max_chars 150
KSL_CKPT=checkpoints/test/best.pt KSL_INDEX=checkpoints/sample_index.pkl KSL_MOTION=data/sample/motions uvicorn api:app --port 8000
```
샘플 동작은 손이 원을 그리는 가짜 동작이라 화면 연결 확인용으로만 쓴다.

## 팀원 모델 (참고)

팀원 SignBridge 저장소에는 AI Hub 16만 문장쌍으로 학습한 KoBART 번역 모델(`/t2g`, 475MB)이 있다. 재난문자로 학습해 산업안전 문장에 맞는지 시험이 필요하고, 모델 파일을 받아야 쓸 수 있다.
