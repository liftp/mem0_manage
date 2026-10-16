#!/bin/bash
# Windows -> WSL 端到端验证 (从 Windows 侧调用)
set -u
API=http://127.0.0.1:8000
KEYS=/mnt/c/install/wb_workspace/mem0/harness-keys.txt

WB=$(grep '^workbuddy=' "$KEYS" | cut -d= -f2)
TR=$(grep '^trae=' "$KEYS" | cut -d= -f2)

echo "=== write via workbuddy key (from Windows) ==="
curl -s -X POST "$API/mem0-agent/memories" \
  -H "X-API-Key: $WB" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Windows 侧验证：本项目用 pnpm 管理依赖"}],"infer":false}' | head -c 300
echo

echo "=== search via trae key (cross-harness) ==="
curl -s -X POST "$API/mem0-agent/search" \
  -H "X-API-Key: $TR" -H 'Content-Type: application/json' \
  -d '{"query":"依赖管理","top_k":3}' | head -c 500
echo

echo "=== scopes ==="
curl -s "$API/mem0-agent/scopes" -H "X-API-Key: $WB" | head -c 300
echo
