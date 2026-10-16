#!/bin/bash
# 快速验证 mem0 API 与增强层路由
set -u
API=http://localhost:8000

code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

echo "setup-status        : $(code $API/auth/setup-status)"
echo "mem0-agent/scopes   : $(code $API/mem0-agent/scopes)"
echo "official /memories  : $(code -X POST $API/memories -H 'Content-Type: application/json' -d '{}')"

curl -s $API/openapi.json -o /tmp/mem0_openapi.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/mem0_openapi.json"))
paths = [p for p in d["paths"] if "mem0-agent" in p]
print("mem0-agent routes   :", paths if paths else "NONE")
PY
