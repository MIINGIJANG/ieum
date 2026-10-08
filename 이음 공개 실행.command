#!/bin/zsh
# 이음 공개 실행: 서버를 켜고, 누구나 접속할 수 있는 임시 주소(https://….trycloudflare.com)를 만듭니다.
# Mac 이 켜져 있고 이 창이 열려 있는 동안만 접속됩니다. 끌 때는 이 창을 닫으세요. (켤 때마다 주소가 바뀜)
cd "$(dirname "$0")"
source ~/.zshrc 2>/dev/null
lsof -ti tcp:8000 | xargs kill 2>/dev/null
python -c "import pdfplumber, pypdfium2, PIL" 2>/dev/null || python -m pip install -q -U pdfplumber pypdfium2 pillow
export KSL_CKPT="${KSL_CKPT:-checkpoints/test/best.pt}"

# 1) 터널 프로그램(cloudflared) 준비: 없으면 처음 한 번만 내려받음
mkdir -p .cloudflared
CF="$(command -v cloudflared)"
if [[ -z "$CF" ]]; then
  CF=.cloudflared/cloudflared
  if [[ ! -x "$CF" ]]; then
    ARCH=$([[ "$(uname -m)" == "arm64" ]] && echo arm64 || echo amd64)
    echo "터널 프로그램 내려받는 중 (처음 한 번)..."
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-$ARCH.tgz" | tar -xz -C .cloudflared \
      || { echo "내려받기 실패. 인터넷 연결을 확인하세요."; read -k 1; exit 1; }
    chmod +x "$CF"
  fi
fi

# 2) 이음 서버 켜기
echo "이음 서버를 켜는 중..."
python -m uvicorn api:app --port 8000 > .cloudflared/server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER $TUNNEL 2>/dev/null' EXIT
until curl -s localhost:8000/health >/dev/null; do
  if ! kill -0 $SERVER 2>/dev/null; then echo "서버가 꺼졌습니다:"; tail -20 .cloudflared/server.log; read -k 1; exit 1; fi
  sleep 1
done

# 3) 공개 주소 만들기
echo "공개 주소 만드는 중..."
: > .cloudflared/tunnel.log
"$CF" tunnel --no-autoupdate --url http://localhost:8000 > .cloudflared/tunnel.log 2>&1 &
TUNNEL=$!
URL=""
for i in {1..60}; do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' .cloudflared/tunnel.log | head -1)
  [[ -n "$URL" ]] && break
  sleep 1
done
if [[ -z "$URL" ]]; then echo "공개 주소를 만들지 못했습니다:"; tail -20 .cloudflared/tunnel.log; read -k 1; exit 1; fi
sleep 3                                   # 주소가 인터넷에 퍼질 때까지 잠깐
echo "$URL" | pbcopy
echo ""
echo "=============================================="
echo "  공개 주소 (클립보드에 복사됨):"
echo "  $URL"
echo "=============================================="
echo "이 주소를 보내면 누구나 바로 접속할 수 있습니다."
echo "이 창을 닫으면 서버와 주소가 모두 꺼집니다."
open "$URL"
wait $SERVER
echo "서버가 꺼졌습니다. 아무 키나 누르면 창이 닫힙니다."; read -k 1
