# Hugging Face 배포와 운영

이음은 Python 서버(검색·답변·동작 합치기)가 필요해서 Hugging Face Spaces(Docker)에 올린다.
Space: `MIINGI/ieum` → https://miingi-ieum.hf.space

## 자동 배포 (보통은 이것만)

`main` 에 올리면 `.github/workflows/deploy-space.yml` 이 아래를 차례로 한다.

1. `pytest tests` + 환각 방지 평가(`scripts/eval/ci_eval.py`) — 기준보다 떨어지면 여기서 멈춤 (배포 안 함)
2. 업로드 폴더 만들기 (`scripts/deploy/make_space.py --no-ckpt`): `api.py`, `ksl/`, `web/`, `data/`, `scripts/`, `requirements.txt` + `deploy/huggingface/` 의 Dockerfile·README
3. Space 에 기록 보관용 토큰 넣기 (`KSL_HF_TOKEN` → 동작 없는 단어 기록을 `MIINGI/ieum-logs` 데이터셋에 보관)
4. 업로드: `data/` `ksl/` `web/` `scripts/` 는 지우고 새로 올림 (옛 파일이 남지 않게, 번역 모델 `checkpoints/` 는 유지)
5. Space 가 멈춰 있으면 다시 켜기 (`scripts/deploy/ensure_running.py`)

빌드가 끝나 사이트가 바뀌기까지 보통 2~3분. 결과는 GitHub Actions 실행 화면에서 본다.

### 필요한 비밀값 (GitHub 저장소 Settings → Secrets and variables → Actions)

| 이름 | 용도 |
|---|---|
| `HF_TOKEN` | Space 업로드·다시 켜기 (Hugging Face **Write** 토큰) |
| `KSL_HF_TOKEN` | Space 가 기록 데이터셋에 쓸 토큰 (없으면 기록은 Space 안에만, 다시 켜면 사라짐) |

Claude 답변 모드 키(`ANTHROPIC_API_KEY`)는 코드가 아니라 **Space** Settings → Variables and secrets 에 넣는다.

## 운영

- **잠자기:** 무료 Space 는 오래 안 쓰면 잠든다. `keepalive-space.yml` 이 6시간마다 확인해서 다시 켜고 한 번 접속한다. 발표 직전에는 한 번 열어 두기 (깨어나는 데 1~2분)
- **첫 질문:** 서버가 켜질 때 예시 질문 답을 미리 만들어 둬서 예시는 바로 답한다. 화면도 로딩 중에 예시 답·출처 이미지·아바타를 미리 받는다
- **오프라인:** 한 번 접속한 기기는 받은 화면·아바타·답을 저장해 두고(`web/sw.js`), 인터넷이 끊기면 그것으로 연다 (예시 질문·전에 물어본 질문만)
- **주간 평가:** `eval-live.yml` 이 매주 배포된 사이트의 `/eval` 로 Claude 답변 모드까지 평가

## 손으로 올리기 (자동 배포를 못 쓸 때)

```bash
python scripts/deploy/make_space.py checkpoints/test/best.pt   # 번역 모델까지 넣을 때 (없으면 --no-ckpt)
pip install -U huggingface_hub && hf auth login                # Write 토큰
hf upload MIINGI/ieum dist/space . --repo-type space
```

## 유의할 점

- **데이터 이용 조건:** `data/sign/motions` 는 AI Hub 데이터에서 뽑은 관절 좌표다. AI Hub 데이터는 재배포가 제한되므로 공개 범위는 이용 약관(결과물 공개 범위)을 확인하고 정한다
- **토큰:** 코드나 저장소에 넣지 않는다. 유출되면 Hugging Face Settings 에서 바로 삭제
- **용량:** 업로드 폴더는 약 25MB. 원본 데이터(`data/raw`)는 빠진다
