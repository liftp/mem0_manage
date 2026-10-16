# Base image pulled through the 1ms mirror (docker.io is unreachable directly).
ARG BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
FROM ${BASE_IMAGE}

WORKDIR /app

# Official server requirements (fastapi/uvicorn/sqlalchemy/alembic/auth...)
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "mem0ai>=2.0,<3.0"

# fastembed (local ONNX embedder) + its onnxruntime dependency
RUN pip install --no-cache-dir "fastembed>=0.3.1"

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

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
