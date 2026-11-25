set dotenv-load := true

[windows]
set shell := ["powershell.exe", "-Command"]

export UV_SYSTEM_CERTS := "1"
export NODE_OPTIONS := "--use-system-ca"

# Запуск бэкенда и фронтенда в prod-режиме (только сервисы приложения)
prod:
    docker compose --profile prod up -d back front

# То же самое, но с пересборкой
prod-build:
    docker compose --profile prod up --build -d back front

# Запуск бэкенда и фронтенда в dev-режиме (всегда с пересборкой)
dev:
    docker compose --profile dev up --build --watch back-dev front-dev

# Локальные БД приложения (postgres + valkey)
db-stand:
    docker compose up -d postgres valkey

# Локальный Keycloak вместо корпоративного (нужен db-stand)
auth-stand:
    docker compose up -d keycloak

# Только Alloy — когда Loki/Grafana/Prometheus уже есть снаружи
monitoring:
    docker compose up -d alloy

# Полный мониторинговый стек в этом compose (Loki, Grafana, Alloy, Prometheus, Alertmanager, SMTP relay)
monitoring-stand:
    docker compose up -d loki grafana alloy prometheus alertmanager smtp-relay

# Локальная проверка дрейфа общих конфигов между тремя стеками и DWH
drift-check:
    python ../infra/check-drift.py

# Nginx (fholzer/nginx-brotli) в этом compose
proxy-stand:
    docker compose up -d nginx

# Установка / обновление зависимостей (uv, bun)
deps:
    uv sync --directory back
    bun install --cwd front

# Линтинг проекта (ruff)
lint:
    uv run --directory back ruff check --fix
    uv run --directory back ruff format
    bun run --cwd front lint
    bun run --cwd front format

# Проверка типов (ty, bun check)
typecheck:
    uv run --directory back ty check --fix
    bun run --cwd front typecheck

# Тестирование (требует just db-stand, тесты используют TEST_DATABASE_URL)
test:
    uv run --directory back pytest
    bun run --cwd front test

# Интеграционное тестирование (требует just prod и заполненную БД).
e2e:
    bun run --cwd front test:e2e --project=smoke

# # Локальный запуск на хосте бэкенда и фронтенда
# # (пока не было необходимости)
# back:
#     uv run --directory back granian --interface asgi --reload --reload-ignore-dirs logs src.main:app --port {{ env_var("BACKEND_PORT") }}

# front:
#     bun run --cwd front dev --hostname 127.0.0.1 --port {{ env_var("FRONT_PORT") }}

# КОМАНДЫ, МОДИФИЦИРУЮЩИЕ БД. ЗАПУСКАТЬ КРАЙНЕ ОСТОРОЖНО!
# Полное пересоздание схемы и сидирование базовых данных (проекты, справочники, пользователи)
seed:
    uv run --directory back -m src.scripts.seed_database --reset

# Пересоздаёт только таблицы проекта «Ремонт» (без drop schema и без
# app_users) и пересидирует справочники и небольшой демонстрационный план.
# «Ремонт»: пересоздание и пересидирование таблиц проекта
seed-renovation:
    uv run --directory back -m src.scripts.seed_database --renovation

# Копирует исходные таблицы КППК (raw.kppk_*) в схему программы,
# пересобирает «Продуктовые позиции» из product_matrix.sql (пары продукт × ДЗО
# и их акцизы), ПЕРЕЗАПИСЫВАЕТ строки «Реализации»/«Себестоимости» за период
# с сентября 2023 по декабрь следующего года и пересчитывает себестоимость.
# Если исходных таблиц нет (чистая/CI-база), создаёт их из back/src/kppk/data/ и наполняет
# демо-строками. Временное решение — должно исчезнуть, когда данные в исходной БД нормализуются.
# КППК: источники, продуктовая матрица и периоды
seed-kppk:
    uv run --directory back -m src.scripts.seed_kppk

# Логистика: только данные, которые ведутся на нашей стороне — проект,
# справочник классов опасности (1..9) и администратор (Евтушенко).
# Внешняя схема dict не читается и не изменяется.
seed-logistics:
    uv run --directory back -m src.scripts.seed_logistics

# Логистика, ТОЛЬКО ДЛЯ ЛОКАЛЬНОЙ РАЗРАБОТКИ: создаёт недостающие таблицы
# внешней схемы dict (countries, orgs, transport_types, vehicle_types,
# transport_types_vehicle_types) и наполняет их демонстрационными строками.
# Без них «Ставки» не работают: миграция 0035 и каждый запрос ставок читают
# эту схему. Ничего не удаляет и не изменяет; перед загрузкой настоящих дампов
# созданные таблицы нужно удалить вручную.
seed-dev-dict:
    uv run --directory back -m src.scripts.seed_dev_dict --yes

# Разовый пересчёт и сохранение расчётных полей
# (и идентификаторов) рабочего плана проекта Ремонт. Нужно после импорта/адопции
# данных и после изменения формул; на PostgreSQL 14 схему менять не нужно.
# «Ремонт»: пересчёт расчётных полей плана
recompute-renovation-plan:
    uv run --directory back -m src.scripts.recompute_renovation_plan

# Применение миграций к БД (alembic)
migrate:
    uv run --directory back -m migrations.upgrade

# Генерация черновика новой миграции от моделей (alembic)
revision message:
    uv run --directory back alembic revision --autogenerate -m "{{message}}"
