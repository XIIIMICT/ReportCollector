set windows-shell := ["powershell.exe", "-Command"]

export UV_SYSTEM_CERTS := "1"

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
    uv run --directory back -m uvicorn src.main:app --reload --port 3000

front:
    bun run --cwd front dev --hostname 127.0.0.1 --port 5000

# ── Данные ──────────────────────────────────────────────────────────────────
# seed — пересоздаёт схему и сидирует базовые данные (проекты, справочники, Евтушенко).
seed:
    uv run --directory back -m src.scripts.seed_database --reset

deps:
    uv --system-certs --directory back sync
    bun install --cwd front

# ── Качество (дублируют шаги CI, но с исправлением) ─────────────────────────
lint:
    uv run --directory back ruff check --fix
    uv run --directory back ruff format
    bun run --cwd front lint:check
    bun run --cwd front format:check

typecheck:
    uv run --directory back ty check
    bun run --cwd front typecheck

# Требует запущенный postgres (just db-standalone) — тесты используют TEST_DATABASE_URL.
test:
    uv run --directory back pytest

# Требует запущенный стек (just prod) и сидированную БД.
e2e:
    bun run --cwd front test:e2e
