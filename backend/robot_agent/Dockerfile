# syntax=docker/dockerfile:1
# Build context is ./backend (so the shared contract/physics in backend/shared is available).
#
# Stage 1 installs the locked dependencies into a venv; stage 2 copies only that venv, so
# nothing used to build (pip cache, compilers) ships in the runtime image. One image, six
# containers in docker-compose.yml, distinguished only by the ROBOT_ID env var.
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY robot_agent/requirements.lock .
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
COPY robot_agent/ .

USER appuser

EXPOSE 8010

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health', timeout=4)"

# ROBOT_ID is required (no default - see main.py): docker-compose.yml sets one per container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
