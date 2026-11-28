#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  printf '설치.command를 먼저 실행해 주세요.\n'
  read -r answer
  exit 1
fi
.venv/bin/python run.py watch
result=$?
printf '\n창을 닫으려면 Enter를 누르세요.'
read -r answer
exit "$result"
