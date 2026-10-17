#!/usr/bin/env bash
# 拉取官方 mem0 dashboard 前端源码到 ./dashboard
#
# 本仓库的 mem0 server 是 mem0ai 2.2.1 的补丁版, dashboard 与 server 版本强耦合,
# 必须用相同版本的源码, 否则 API schema 漂移会导致白屏。
#
# 用法 (在 WSL 终端, hch 身份):
#   cd /usr/local/soft/mem0 && bash scripts/fetch_dashboard.sh
# 或覆盖版本:
#   MEM0_TAG=v2.2.1 bash scripts/fetch_dashboard.sh
set -euo pipefail

MEM0_TAG="${MEM0_TAG:-v2.2.1}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$SRC_DIR/dashboard"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

# WSL 非交互 shell 不会加载 .bashrc, 手动导出代理(按需修改为你实际的 WSL-NAT-PROXY)
export HTTP_PROXY="${HTTP_PROXY:-http://172.20.240.1:17890}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://172.20.240.1:17890}"
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
git config --global http.proxy "$HTTP_PROXY" 2>/dev/null || true
git config --global https.proxy "$HTTPS_PROXY" 2>/dev/null || true

echo "==> sparse-clone mem0ai/mem0 @ $MEM0_TAG"
git clone --filter=blob:none --sparse --depth 1 --branch "$MEM0_TAG" \
  https://github.com/mem0ai/mem0.git "$TMP/repo"

cd "$TMP/repo"
git sparse-checkout set server/dashboard
git checkout "$MEM0_TAG" 2>/dev/null || true

echo "==> copy server/dashboard -> $DEST"
mkdir -p "$DEST"
cp -r server/dashboard/. "$DEST/"

echo "==> done. dashboard source at: $DEST"
echo "    接着: docker compose -f docker-compose.yml -f docker-compose.dashboard.yml up -d --build dashboard"
