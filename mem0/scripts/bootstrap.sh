#!/bin/bash
# ============================================================
# mem0 Agent 全局记忆系统 - 一键部署
# 用法: ./scripts/bootstrap.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
API_URL="http://localhost:${MEM0_PORT:-8000}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[bootstrap]${NC} $*"; }
warn() { echo -e "${YELLOW}[bootstrap]${NC} $*"; }
die()  { echo -e "${RED}[bootstrap]${NC} $*" >&2; exit 1; }

# ---------- 0. docker 权限 (WSL: han 默认不在 docker 组) ----------
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    DOCKER="sudo docker"
  else
    warn "docker 需要 sudo 权限 (WSL 默认), 请输入密码"
    sudo -v || die "sudo 认证失败"
    DOCKER="sudo docker"
  fi
fi
$DOCKER info >/dev/null 2>&1 || die "docker 不可用"

# ---------- 1. .env ----------
if [ ! -f .env ]; then
  [ -f .env.example ] || die ".env.example 不存在"
  cp .env.example .env
  log "已创建 .env (从 .env.example 复制)"
fi
set -a; . ./.env; set +a

[ -n "${DEEPSEEK_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ] || warn ".env 未设置 DEEPSEEK_API_KEY / OPENAI_API_KEY (LLM 调用会失败)"

if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n')"
  # 回填 .env
  if grep -q '^JWT_SECRET=' .env; then
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" .env
  else
    echo "JWT_SECRET=${JWT_SECRET}" >> .env
  fi
  log "已生成 JWT_SECRET 并回填 .env"
fi

# ---------- 2. 起容器 ----------
log "启动 docker compose (postgres + mem0-server) ..."
$DOCKER compose up -d --build 2>&1 | tee /tmp/mem0_bootstrap.log

log "等待 API 就绪 ..."
for i in $(seq 1 60); do
  if curl -fsS "$API_URL/auth/setup-status" >/dev/null 2>&1; then break; fi
  sleep 2
  [ $i -eq 60 ] && die "API 未就绪, 查看日志: $DOCKER compose logs mem0"
done
log "API 就绪: $API_URL"

# ---------- 3. 注册 admin ----------
# 注意: pydantic EmailStr 拒绝保留域名 (.local/.localhost/.test 等), 用 .dev
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@mem0.dev}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-change-me-admin}"
NEEDS_SETUP=$(curl -s "$API_URL/auth/setup-status" | python3 -c "import sys,json;print(json.load(sys.stdin).get('needsSetup',True))" 2>/dev/null || echo True)

TOKEN=""
if [ "$NEEDS_SETUP" = "True" ]; then
  log "注册 admin: $ADMIN_EMAIL"
  REG=$(curl -s -X POST "$API_URL/auth/register" -H 'Content-Type: application/json' \
    -d "{\"name\":\"admin\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}")
  TOKEN=$(echo "$REG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
fi
if [ -z "$TOKEN" ]; then
  LOGIN=$(curl -s -X POST "$API_URL/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}")
  TOKEN=$(echo "$LOGIN" | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
fi
[ -n "$TOKEN" ] || die "admin 登录失败 (检查 .env 中 ADMIN_PASSWORD)"
log "admin 登录成功"

# ---------- 4. 创建 per-harness API keys ----------
# keys 保存在 .env 旁边的 harness-keys.txt (仅本机)
# 注意: 增强层查的是 harness_keys 表, 不是官方 api_keys 表, 必须在容器内生成
KEYS_FILE="$ROOT/harness-keys.txt"
log "创建 harness API keys (写入 harness_keys 表) -> $KEYS_FILE"
: > "$KEYS_FILE"
HARNESSES="workbuddy qoder trae zcode codex claude-code deepseek-coder"
for h in $HARNESSES; do
  KEY=$($DOCKER compose exec -T mem0 python -c "
from extra.identity import create_harness_key
print(create_harness_key('$h'))
" 2>/dev/null | tail -1)
  if [ -n "$KEY" ]; then
    echo "$h=$KEY" >> "$KEYS_FILE"
  else
    warn "harness key 创建失败: $h"
  fi
done
chmod 644 "$KEYS_FILE"
log "harness keys 已写入 $KEYS_FILE"

# ---------- 5. 冒烟测试 ----------
log "运行冒烟测试 ..."
WB_KEY=$(grep '^workbuddy=' "$KEYS_FILE" | cut -d= -f2)
TR_KEY=$(grep '^trae=' "$KEYS_FILE" | cut -d= -f2)
MEM0_API_URL="$API_URL" \
  WORKBUDDY_KEY="$WB_KEY" \
  TRAE_KEY="$TR_KEY" \
  python3 scripts/smoke_test.py || warn "冒烟测试失败 (可手动重跑: python3 scripts/smoke_test.py)"

cat <<EOF

$(printf '=%.0s' {1..60})
  mem0 Agent 全局记忆系统部署完成

  API:      $API_URL  (OpenAPI: $API_URL/docs)
  Admin:    $ADMIN_EMAIL / $ADMIN_PASSWORD
  Harness keys: $KEYS_FILE
$(printf '=%.0s' {1..60})

EOF
