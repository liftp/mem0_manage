#!/bin/bash
# 最终验收: 干净重启 -> 等 API -> 冒烟测试 -> 汇总状态
set -u
cd /usr/local/soft/mem

echo "=== 1. restart stack ==="
echo "12345" | sudo -S docker compose restart mem0 postgres 2>&1 | tail -3

echo "=== 2. wait for API ==="
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/auth/setup-status 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "ready after ${i}0s"; break; }
  sleep 10
done

echo "=== 3. smoke test ==="
./scripts/run_smoke.sh

echo "=== 4. container status ==="
echo "12345" | sudo -S docker compose ps 2>&1 | tail -4

echo "=== 5. deployed files ==="
echo "12345" | sudo -S ls -la "$PWD" | tail -20
