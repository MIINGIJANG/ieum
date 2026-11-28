#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo 'README의 설치 절차로 가상환경을 준비하세요.'
  read -r 'reply?엔터를 누르면 닫습니다.'
  exit 1
fi
exec .venv/bin/python run.py
