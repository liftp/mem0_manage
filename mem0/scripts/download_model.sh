#!/bin/bash
# 在 WSL 宿主机下载 fastembed 模型文件到 server/models/<org>-<name>/
# 彻底绕开 xet CAS (hf-mirror 下 401), 只走普通 HTTP/LFS
# 目录名约定与 server/main.py 的 FASTEMBED_MODEL_DIR 一致: '/' -> '-'
set -euo pipefail

MODEL="${1:-snowflake/snowflake-arctic-embed-xs}"
MODEL_DIR_NAME="${MODEL//\//-}"
MODEL_DIR="/usr/local/soft/mem/server/models/${MODEL_DIR_NAME}"
REPO="$MODEL"
E="https://hf-mirror.com"

echo "12345" | sudo -S mkdir -p "$MODEL_DIR/onnx"

dl() {
  local fn="$1"
  local dst="$MODEL_DIR/$fn"
  echo "12345" | sudo -S mkdir -p "$(dirname "$dst")"
  echo "12345" | sudo -S curl -fL --retry 3 --max-time 900 \
    -o "$dst" "$E/$REPO/resolve/main/$fn"
  echo "12345" | sudo -S ls -la "$dst"
}

# 仓库文件清单 (来自 hf-mirror API tree)
dl config.json
dl config_sentence_transformers.json
dl tokenizer.json
dl onnx/model.onnx
dl onnx/model_quantized.onnx

# special_tokens_map.json / vocab.txt 等 (若无则忽略)
for f in special_tokens_map.json vocab.txt tokenizer_config.json; do
  echo "12345" | sudo -S curl -fsSL -o "$MODEL_DIR/$f" "$E/$REPO/resolve/main/$f" \
    && echo "got $f" || echo "skip $f (404)"
done

echo "=== 最终目录 ==="
echo "12345" | sudo -S find "$MODEL_DIR" -type f -exec ls -la {} \;
