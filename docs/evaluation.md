# 테스트·평가·점검 도구

## 자동 (GitHub Actions)

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| `test.yml` | 푸시·PR 마다 | `pytest tests` + 웹 JS 문법 검사 |
| `deploy-space.yml` | `main` 에 올릴 때 | `pytest` → `scripts/eval/ci_eval.py` 통과해야 Hugging Face 에 배포 → Space 깨우기 |
| `keepalive-space.yml` | 6시간마다 | Space 가 멈춰 있으면 다시 켬 |
| `eval-live.yml` | 매주 | 배포된 사이트의 `/eval` 로 Claude 답변 모드까지 포함한 평가 (`scripts/eval/live_eval.py`) |

### `pytest tests` (약 1분 20초, 번역 모델 torch 필요 없음)
- 검색 Recall@3 기준, 절 제목 인식, 답변 거절 (문서 밖 28개 모두 거절)
- 동작 이어붙이기(연결 구간·멈춤·쉼·필터), 손 모양 파일, 팀원 자료 가져온 단어
- 수어 어순·문법 표정, 수어 문장 고르기·검수(가짜 Claude 응답), 블록 완결성
- 수어 블록 회귀 사례 (`tests/test_sign_cases.py`: 2미터 → 숫자+미터 한 블록, 4시간 → 시간 수어, 방법들 → 방법) + 대체 표현 깨짐
- API 응답 형식, 동작 없는 단어 기록·보관

### `scripts/eval/ci_eval.py` (배포 관문)
번역 모델·Claude 없이(규칙 기반, 비용 0원) 돌려서 `data/eval/baseline.json` 보다 떨어지면 배포하지 않는다.

| 항목 | 기준 |
|---|---|
| 문서 안 질문 40개에 답함 | ≥ 39 |
| 정답 쪽이 출처에 있음 (정답 쪽 표기된 38개) | ≥ 36 |
| 문서 밖 입력 28개에 답함 (환각) | ≤ 0 |
| 안전 안내 문장 10개 번역 | ≥ 10 |

점수를 일부러 바꿨으면 `baseline.json` 을 같이 고친다. 결과 표와 예시 질문 수어 블록 점검표는 Actions 요약에 나온다.

## 손으로 돌리는 점검

| 도구 | 하는 일 |
|---|---|
| `python scripts/eval/check_signs.py` | 대체 표현이 동작 없는 글로스를 가리키는지(조용한 실패, `--strict` 면 실패), 단어표의 회색 글로스, 말과 글로스가 글자를 같이 쓰지 않는 의심 대응 목록 → 수어를 아는 사람이 확인 |
| `python scripts/eval/e2e_devices.py --url http://localhost:8000` | 휴대폰·태블릿 세로/가로·PC 에서 첫 화면 → 답변·블록·아바타·가로 넘침 점검, 화면 저장 (`--dark` 다크 모드) |
| `python scripts/eval/eval_retrieval.py` | 검색 Recall@1/3/5 (`docs/retrieval.md`) |
| `python scripts/eval/eval_abstain.py` | 답변 거절 조치 전·후 비교 (`docs/hallucination_guard.md`) |
| `python scripts/eval/sweep_threshold.py` | 거절 기준값을 바꿔 가며 비교 |
| 서버 `/blocks.html` | 예시 질문마다 블록·점검 결과·Claude 수어 문장을 쓰지 않은 이유 |
| 서버 `/missing.html` | 동작 없는 단어·대체 표현이 실제로 나온 횟수 |
| 서버 `/llm_check` | Claude 연결 확인 (질문 1번 비용) |
