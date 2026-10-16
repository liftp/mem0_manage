#!/bin/bash
# 将 mem0 官方 server 源码同步到 server/ (相对仓库根)
set -e
cd "$(dirname "$0")/.."
mkdir -p server/alembic/versions server/scripts

FILES=(
  "server/main.py"
  "server/requirements.txt"
  "server/Dockerfile"
  "server/.env.example"
  "server/README.md"
  "server/Makefile"
  "server/dev.Dockerfile"
  "server/docker-compose.yaml"
  "server/db.py"
  "server/models.py"
  "server/auth.py"
  "server/errors.py"
  "server/rate_limit.py"
  "server/telemetry.py"
  "server/schemas.py"
  "server/server_state.py"
  "server/alembic.ini"
  "server/alembic/env.py"
  "server/routers/__init__.py"
  "server/routers/api_keys.py"
  "server/routers/auth.py"
  "server/routers/entities.py"
  "server/routers/requests.py"
  "server/scripts/seed.sh"
  "server/scripts/reset_admin_password.py"
  "server/scripts/prune_request_logs.py"
  "server/alembic/versions/001_create_users_and_api_keys.py"
  "server/alembic/versions/002_create_request_logs.py"
  "server/alembic/versions/003_create_settings.py"
  "server/alembic/versions/004_unique_admin_role.py"
  "server/alembic/versions/005_refresh_token_jtis.py"
  "server/alembic/versions/006_request_logs_brin.py"
)

for f in "${FILES[@]}"; do
  mkdir -p "$(dirname "$f")"
  code=$(curl -s -o "$f" -w '%{http_code}' --max-time 30 "https://cdn.jsdelivr.net/gh/mem0ai/mem0@main/$f")
  echo "$f -> $code ($(stat -c%s "$f" 2>/dev/null) bytes)"
  if [ "$code" != "200" ]; then echo "FAILED: $f"; rm -f "$f"; fi
done
echo "done"
