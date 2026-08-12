FROM ghcr.io/astral-sh/uv:python3.14-alpine AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

FROM base AS deps
# Если SSL_ISSUES_WHEN_INSTALLING_DEPS не пуст — ставим системные сертификаты
# и включаем UV_SYSTEM_CERTS=1 для установки зависимостей.
ARG SSL_ISSUES_WHEN_INSTALLING_DEPS=""
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$SSL_ISSUES_WHEN_INSTALLING_DEPS" ]; then export UV_SYSTEM_CERTS=1; fi \
    && uv sync --frozen --no-dev --no-install-project

FROM python:3.14-alpine AS pre-prod
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN addgroup -S appgroup && adduser -S -G appgroup -D appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup src/ src/
COPY --chown=appuser:appgroup --from=deps /app/.venv/ ./.venv/
USER appuser

FROM pre-prod AS dev
ENTRYPOINT ["uvicorn", "src.main:app", "--reload", "--host", "0.0.0.0", "--port", "6000"]

FROM pre-prod AS prod
ENTRYPOINT ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "6000"]
