#!/bin/zsh
# 이음 실행: 더블클릭하면 서버를 켜고 브라우저를 엽니다. 끌 때는 이 창을 닫으세요.
cd "$(dirname "$0")/../.."                         # 저장소 맨 위 폴더에서 실행
source ~/.zshrc 2>/dev/null                       # anaconda 파이썬 사용
lsof -ti tcp:8000 | xargs kill 2>/dev/null        # 이미 켜진 이음 서버가 있으면 종료
python -c "import pdfplumber, pypdfium2, PIL, kiwipiepy" 2>/dev/null || python -m pip install -q -U pdfplumber pypdfium2 pillow kiwipiepy   # 출처 표·원본 이미지용 (처음 한 번)
export KSL_CKPT="${KSL_CKPT:-checkpoints/test/best.pt}"
( until curl -s localhost:8000/health >/dev/null; do sleep 1; done; open "http://localhost:8000" ) &
echo "이음 서버를 켜는 중... 브라우저가 자동으로 열립니다. (끄려면 이 창을 닫으세요)"
python -m uvicorn api:app --port 8000
echo ""
echo "이음 서버가 꺼졌습니다. 위에 나온 메시지를 확인하세요. (아무 키나 누르면 창이 닫힙니다)"
read -k 1
