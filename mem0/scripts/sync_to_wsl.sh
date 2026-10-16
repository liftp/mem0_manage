#!/bin/bash
# 同步工作区 -> WSL /usr/local/soft/mem (排除运行时产物)
set -euo pipefail
SRC="/mnt/c/install/wb_workspace/mem0"
DST="/usr/local/soft/mem"

if [ ! -d "$SRC" ]; then
  echo "source not found: $SRC (are we inside WSL?)"
  exit 1
fi

if [ -w "$DST" ] || sudo -n true 2>/dev/null; then
  SUDO=""
  [ -w "$DST" ] || SUDO="sudo"
  $SUDO mkdir -p "$DST"
  # --exclude: 运行时状态 (容器卷/.env/keys/logs)
  $SUDO rsync -a --delete \
    --exclude '.git/' \
    --exclude '.trae/' \
    --exclude '__pycache__/' \
    --exclude '.env' \
    --exclude 'harness-keys.txt' \
    --exclude '*.pyc' \
    "$SRC/" "$DST/"
  echo "synced -> $DST"
else
  echo "need sudo for $DST"
  exit 1
fi
