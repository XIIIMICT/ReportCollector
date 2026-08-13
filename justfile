set windows-shell := ["powershell.exe", "-Command"]

export UV_SYSTEM_CERTS := "1"
export NODE_OPTIONS := "--use-system-ca"

# ── Docker (профили prod/dev поднимают только приложение; внешние
#    сервисы Postgres, Keycloak, Loki, Grafana запускаются отдельно
#    через профиль standalone) ───────────────────────────────────────────────
prod:
    docker compose --profile prod up --build -d

prod-build:
    docker compose --profile prod up --build -d

dev:
    docker compose --profile dev up --build --watch

db-standalone:
    docker compose --profile standalone up -d postgres

keycloak-standalone:
    docker compose --profile standalone up -d keycloak

logging-standalone:
    docker compose --profile standalone up -d loki grafana alloy

down:
    docker compose --profile standalone down

clean:
    docker compose down --volumes --remove-orphans

# ── Локальный запуск на хосте ──────────────────────────────────────────────
back:
    uv run --directory back -m uvicorn src.main:app --reload --port 6000

front:
    bun run --cwd front dev --hostname 127.0.0.1 --port 5000

# ── Данные ──────────────────────────────────────────────────────────────────
# seed — пересоздаёт схему и сидирует базовые данные (проекты, справочники, Евтушенко).
seed:
    uv run --directory back -m src.scripts.seed_database --reset

# migrate — применяет миграции к существующей схеме (добавляет collab-таблицы).
# С хоста POSTGRES_HOST из .env (host.docker.internal) не резолвится,
# поэтому переопределяем на localhost; в CI миграция идёт внутри контейнера.
migrate:
    $env:POSTGRES_HOST = "localhost"
    uv run --directory back alembic upgrade head

# revision — генерирует черновик новой миграции от моделей.
revision message:
    uv run --directory back alembic revision --autogenerate -m "{{message}}"

deps:
    uv sync --directory back
    bun install --cwd front

# ── Качество (дублируют шаги CI, но с исправлением) ─────────────────────────
lint:
    uv run --directory back ruff check --fix
    uv run --directory back ruff format
    bun run --cwd front lint
    bun run --cwd front format

typecheck:
    uv run --directory back ty check
    bun run --cwd front typecheck

# Требует запущенный postgres (just db-standalone) — тесты используют TEST_DATABASE_URL.
test:
    uv run --directory back pytest

# Требует запущенный стек (just prod) и сидированную БД.
e2e:
    bun run --cwd front test:e2e
