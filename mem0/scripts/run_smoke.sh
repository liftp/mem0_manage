#!/bin/bash
# 重新运行端到端冒烟测试 (使用已有 harness-keys.txt)
set -u
cd /usr/local/soft/mem

WB=$(grep '^workbuddy=' harness-keys.txt | cut -d= -f2)
TR=$(grep '^trae=' harness-keys.txt | cut -d= -f2)

if [ -z "$WB" ] || [ -z "$TR" ]; then
  echo "missing keys in harness-keys.txt"
  exit 1
fi

echo "12345" | sudo -S env MEM0_API_URL=http://localhost:8000 \
  WORKBUDDY_KEY="$WB" TRAE_KEY="$TR" \
  python3 scripts/smoke_test.py
