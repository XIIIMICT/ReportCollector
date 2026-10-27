set windows-shell := ["powershell.exe", "-Command"]
# Порта берём из корневого .env (BACKEND_PORT / FRONT_PORT) — те же,
# что уходят в compose.yaml; без них рецепты падают, а не молча используют дефолт.
set dotenv-load := true

export UV_SYSTEM_CERTS := "1"
export NODE_OPTIONS := "--use-system-ca"

# ── Docker (профили prod/dev поднимают только приложение; внешние
#    сервисы Postgres, Keycloak, Loki, Grafana запускаются отдельно
#    через профиль standalone) ───────────────────────────────────────────────
prod:
    docker compose --profile prod up -d

prod-build:
    docker compose --profile prod up --build -d

dev:
    docker compose --profile dev up --build --watch

db:
    docker compose --profile standalone up -d postgres valkey

keycloak:
    docker compose --profile standalone up -d keycloak

monitoring:
    docker compose --profile standalone up -d loki grafana alloy prometheus alertmanager

down:
    docker compose --profile standalone down

clean:
    docker compose down --volumes --remove-orphans

# ── Данные ──────────────────────────────────────────────────────────────────
# seed — пересоздаёт схему и сидирует базовые данные (проекты, справочники, Евтушенко).
seed:
    uv run --directory back -m src.scripts.seed_database --reset

# seed-renovation — перезатирает только таблицы реновации (без drop schema и без
# app_users) и пересидирует их из «РБ ПОФ»-файлов.
seed-renovation:
    uv run --directory back -m src.scripts.seed_database --renovation

# seed-renovation-plan — переимпортирует только рабочий план реновации
# (активы, справочники и пользователи сохраняются). Расчётные поля
# материализуются в конце импорта.
seed-renovation-plan:
    uv run --directory back -m src.scripts.seed_database --renovation-plan

# seed-kppk — перекопирует исходные таблицы КППК (raw.kppk_*) в схему программы,
# пересобирает «Продуктовые позиции» из product_matrix.sql (пары продукт × ДЗО
# и их акцизы), ПЕРЕЗАПИСЫВАЕТ строки «Реализации»/«Себестоимости» за период
# с сентября 2023 по декабрь следующего года и пересчитывает себестоимость.
# Если исходных таблиц нет (чистая/CI-база), создаёт их из back/src/kppk/data/
# и наполняет демо-строками. Другие проекты (схема dict) не трогает.
seed-kppk:
    uv run --directory back -m src.scripts.seed_kppk

# recompute-renovation-plan — разово пересчитывает и сохраняет расчётные поля
# (и идентификаторы) рабочего плана реновации. Нужно после импорта/адопции
# данных и после изменения формул; на PostgreSQL 14 схему менять не нужно.
recompute-renovation-plan:
    uv run --directory back -m src.scripts.recompute_renovation_plan

# migrate — применяет миграции к существующей схеме.
# POSTGRES_HOST берётся из .env (для сервера — реальный адрес БД, не localhost).
migrate:
    uv run --directory back -m migrations.upgrade

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

# Требует запущенный postgres (just db) — тесты используют TEST_DATABASE_URL.
test:
    uv run --directory back pytest
    bun run --cwd front test

# Требует запущенный стек (just prod) и сидированную БД.
e2e:
    bun run --cwd front test:e2e --project=smoke

# ── Локальный запуск на хосте (пока не было необходимости) ──────────────────
# back:
#     uv run --directory back -m uvicorn src.main:app --reload --port {{ env_var("BACKEND_PORT") }}

# front:
#     bun run --cwd front dev --hostname 127.0.0.1 --port {{ env_var("FRONT_PORT") }}
