# Base image pulled through the 1ms mirror (docker.io is unreachable directly).
ARG BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
FROM ${BASE_IMAGE}

# --- 代理透传 (WSL-NAT-PROXY) ---
# 构建容器默认没有代理, 而本机出网需走 Windows 主机的代理 ($HOST_IP:17890)。
# 从已 export 了 HTTP_PROXY/HTTPS_PROXY 的 shell 跑 `docker compose build` 时,
# compose 会把这两个变量作为 build-arg 传入(见 docker-compose.yml 的 build.args),
# 这里注入为构建期环境变量, pip 即可经代理访问镜像源。
# 注意: 这些变量只在构建期生效, 文末已清空, 不影响运行时容器。
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ENV HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY \
    http_proxy=$HTTP_PROXY https_proxy=$HTTPS_PROXY

# --- 国内 pip 镜像增强 ---
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV PIP_RETRIES=10
ENV PIP_TIMEOUT=60
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple \
    && pip config set global.trusted-host mirrors.aliyun.com \
    && pip config set global.retries 10 \
    && pip config set global.timeout 60

WORKDIR /app

# Official server requirements (fastapi/uvicorn/sqlalchemy/alembic/auth...)
COPY server/requirements.txt .
RUN pip install --no-cache-dir --no-input -r requirements.txt

# mem0ai (split out so a failure points at the exact package)
RUN pip install --no-cache-dir --no-input "mem0ai>=2.0,<3.0"

# fastembed (local ONNX embedder) + its onnxruntime dependency
RUN pip install --no-cache-dir --no-input "fastembed>=0.3.1"

# Official server code
COPY server/ .

# Enhancement layer (identity / scope / ACL / audit)
COPY server/extra/ /app/extra/

# Pre-baked fastembed model — vendored in the repo (server/models/).
# The default fastembed repo (Qdrant/bge-small-en-v1.5-onnx-Q) only serves its
# ONNX weights through the xet CAS backend, which 401s behind HF mirrors, so
# the model files are downloaded once by scripts/download_model.sh and shipped
# with the source. Zero network access is needed at build or run time.
COPY server/models/ /app/models/
RUN chmod -R a+rX /app/models

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

# 清空构建期代理, 避免污染运行时容器 (运行时如需代理可在此保留或改由 compose 注入)
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy=

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
