# syntax=docker/dockerfile:1
# Build context is ./backend (so the shared contract in backend/shared is available).
#
# Stage 1 installs the locked dependencies into a venv; stage 2 copies only that venv, so
# nothing used to build (pip cache, compilers) ships in the runtime image.
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY lidar_service/requirements.lock .
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
COPY lidar_service/ .

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health', timeout=4)"

# Single worker: simulation state lives in process memory.
# --ws-per-message-deflate false: this stream is an internal hop, and uvicorn compresses each
# frame once per viewer synchronously on the event-loop thread (~11 ms per 160 KB frame, which
# stalled the loop ~35 ms/tick with 3 viewers).  Compression on the public leg belongs to the gateway.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--ws-per-message-deflate", "false"]
