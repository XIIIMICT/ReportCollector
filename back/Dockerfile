ARG PYTHON_VERSION=3.15
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-alpine AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    --allow-insecure-host pypi.org \
    --allow-insecure-host files.pythonhosted.org

FROM python:${PYTHON_VERSION}-alpine AS pre-prod
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
# Порт, на котором слушает uvicorn. Единственная точка в Dockerfile:
# значение задаёт compose.yaml из BACKEND_PORT в .env.
ARG BACKEND_PORT=8000
ENV BACKEND_PORT=${BACKEND_PORT}

RUN addgroup -S appgroup && adduser -S -G appgroup -D appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup src/ src/
COPY --chown=appuser:appgroup --from=deps /app/.venv/ ./.venv/
USER appuser

FROM pre-prod AS dev
CMD ["sh", "-c", "exec uvicorn src.main:app --reload --host 0.0.0.0 --port \"$BACKEND_PORT\""]

FROM pre-prod AS prod
CMD ["sh", "-c", "exec uvicorn src.main:app --host 0.0.0.0 --port \"$BACKEND_PORT\""]
