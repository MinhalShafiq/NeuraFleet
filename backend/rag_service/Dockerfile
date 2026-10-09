# syntax=docker/dockerfile:1
# Build context is ./backend (so the shared contract in backend/shared is available).
#
# Stage 1 installs the locked dependencies into a venv; stage 2 copies only that venv, so
# nothing used to build (pip cache, compilers) ships in the runtime image.
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY rag_service/requirements.lock .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.lock

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY shared/ /app/shared/
COPY rag_service/ .

# Persistent Chroma directory, owned by the runtime user
RUN mkdir -p /app/chroma_data && chown appuser:appuser /app/chroma_data

USER appuser

# Bake Chroma's default embedding model (ONNX MiniLM, ~80 MB) into the image so startup
# needs no network and the container can run with a read-only root filesystem.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm-up'])"

EXPOSE 8003

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health', timeout=4)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
