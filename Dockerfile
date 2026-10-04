# syntax=docker/dockerfile:1
ARG EDITION=search
FROM oven/bun:1.4.2 AS frontend-build
ARG EDITION
WORKDIR /build
COPY package.json bun.lock ./
COPY frontend/package.json ./frontend/package.json
RUN bun install --frozen-lockfile --filter './frontend'
COPY frontend/src/ ./frontend/src/
COPY frontend/public/ ./frontend/public/
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./frontend/
COPY examples/images/ ./examples/images/
RUN case "$EDITION" in search|web|studio) ;; *) exit 2 ;; esac \
    && cd frontend && bunx tsc --noEmit && bunx vite build --mode "$EDITION"

FROM python:3.12-slim-bookworm AS python-build
ARG EDITION
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml
ARG UV_FIND_LINKS
ARG UV_NO_INDEX=false
RUN --mount=type=cache,target=/root/.cache/uv \
    case "$EDITION" in search) set -- ;; web) set -- --extra web ;; studio) set -- --extra agent ;; *) exit 2 ;; esac \
    && uv export --locked --package multimodal-backend --no-dev --no-emit-workspace "$@" \
      --emit-index-url --output-file /tmp/requirements.txt > /dev/null \
    && uv venv /opt/venv \
    && uv pip sync --python /opt/venv/bin/python --require-hashes \
      --index-strategy unsafe-best-match /tmp/requirements.txt
COPY backend/app/ ./backend/app/
COPY scripts/package_backend.py ./scripts/package_backend.py
RUN python scripts/package_backend.py --edition "$EDITION" --output /build/packaged/app

FROM python:3.12-slim-bookworm AS runtime
ARG EDITION
ENV PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LOGFIRE_ENVIRONMENT=production \
    EXPLORER_GATEWAY_URL=http://127.0.0.1:7860 EXPLORER_PUBLIC_URL=http://127.0.0.1:7860
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data && chown -R appuser:appuser /app
WORKDIR /app
COPY --from=python-build /opt/venv /opt/venv
COPY --from=python-build --chown=appuser:appuser /build/packaged/app/ ./backend/app/
COPY --chown=appuser:appuser backend/alembic.ini ./backend/alembic.ini
COPY --chown=appuser:appuser cronjob/match_images.py ./cronjob/match_images.py
COPY --chown=appuser:appuser uv.lock ./uv.lock
COPY --from=frontend-build --chown=appuser:appuser /build/frontend/dist/${EDITION}/ ./frontend/dist/${EDITION}/
# The generated launcher fixes the entry point at build time.
RUN case "$EDITION" in \
      search) command='python -m uvicorn app.main:app --host 0.0.0.0 --port 7860' ;; \
      web) command='python -m uvicorn app.web:app --host 0.0.0.0 --port 7860' ;; \
      studio) command='python -m app.space' ;; *) exit 2 ;; esac \
    && printf '#!/bin/sh\nexec %s\n' "$command" > /app/start && chmod 755 /app/start
USER appuser
EXPOSE 7860
CMD ["/app/start"]
