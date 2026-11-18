#!/bin/zsh
cd "${0:A:h}" || exit 1
python3 setup_local.py
result=$?
printf '\n창을 닫으려면 Enter를 누르세요.'
read -r answer
exit "$result"
