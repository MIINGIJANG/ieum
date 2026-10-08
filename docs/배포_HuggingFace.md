# Hugging Face Spaces 배포

이음은 Python 서버(검색·번역·동작 합치기)가 필요해서 GitHub Pages 대신 Hugging Face Spaces(Docker)에 올린다.

## 순서 (Mac 터미널, 이음 폴더에서)

1. huggingface.co 가입 → Settings → Access Tokens 에서 **Write** 토큰 만들기
2. Space 만들기: New Space → 이름 `ieum`, SDK **Docker** (Blank), 하드웨어 **CPU basic (무료)**, 공개 범위 **Private**
3. 업로드할 폴더 만들기
   ```
   python scripts/make_space.py checkpoints/test/best.pt
   ```
4. 로그인 후 업로드
   ```
   pip install -U huggingface_hub
   hf auth login            # 1번 토큰 붙여넣기
   hf upload <아이디>/ieum dist/space . --repo-type space
   ```
5. Space 페이지 Logs 에서 빌드(처음 5~10분) → Running 이 되면 App 탭에서 확인
6. 고친 뒤 다시 올릴 때: 3·4번의 make_space, hf upload 만 다시 실행

## 유의할 점

- **공개 범위:** `data/motions` 는 AI Hub 데이터에서 뽑은 관절 좌표다. AI Hub 데이터는 재배포가 제한되므로 Space 는 **Private** 으로 둔다. Private Space 는 로그인한 본인·초대된 사람(조직 멤버)만 볼 수 있다 → 조원은 Organization 을 만들어 초대.
  공개 시연이 꼭 필요하면 AI Hub 이용 약관(결과물 공개 범위)을 먼저 확인할 것.
- **잠자기:** 무료 Space 는 한동안 접속이 없으면 잠든다. 발표 직전에 한 번 열어서 깨워 둘 것(깨어나는 데 1~2분).
- **첫 번역:** 서버가 뜬 뒤 첫 문장 번역은 모델을 불러오느라 몇 초 걸린다(질문 답변은 바로).
- **토큰:** 토큰은 코드나 저장소에 넣지 말 것. 유출되면 Settings 에서 바로 삭제.
- **용량:** 올라가는 폴더는 약 26MB. 원본 데이터(`data/raw`), 여자 아바타, 한글 이름 파일은 빠진다.
- **API 키:** LLM 답변 모드를 쓰려면 키를 코드가 아니라 Space Settings → Variables and secrets 에 넣는다.
