# ReportCollector Architecture

Raw-material planning system: Excel-like forms with real-time collaborative editing. Monorepo: `front/` (Next.js), `back/` (FastAPI), `infra/` (compose of external services), `docs/`.

## System diagram

```
Browser ── https://<host> ──► nginx (outside Docker, on prod)
                              ├── /              → front (Next.js standalone, :5000)
                              └── /api/ws/       → back (FastAPI/uvicorn, :8000)
                              (HTTPS + WSS terminated in nginx)
front ── BACKEND_INTERNAL_URL ──► back (/api/*, server-side, signed headers)
back  ── Postgres (asyncpg, schema from POSTGRES_SCHEMA)
     ── Valkey/Redis (leases, presence, undo stacks, pub/sub; VALKEY_URL empty → in-memory)
     ── Loki ← Alloy (logs; Grafana for viewing)
Keycloak ── OIDC (login; local container or corporate)
```

## Services and compose profiles

`compose.yaml` (name: `reportcollector`) `include`s `infra/compose.external-services.yaml` (postgres, keycloak, valkey) and `infra/compose.alloy.yaml` (loki, grafana, alloy).

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `rc-back` / `rc-back-dev` | prod / dev | FastAPI, uvicorn, port `BACKEND_PORT` |
| `front` / `front-dev` | `rc-front` / `rc-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload) |
| postgres, keycloak, valkey | `rc-postgres` etc. | standalone | external dependencies (`just db`, `just keycloak`) |
| loki, grafana, alloy | — | standalone | logs (`just logging`) |

Key point: **ports and hosts are set once in the root `.env`** and spread into the Dockerfiles (build-arg `PORT`/`BACKEND_PORT`), port publishing, healthchecks, and internal URLs. All mandatory (`:?` in compose — fails with a clear error instead of staying silent). No defaults in Dockerfiles.

## Environment variables (root `.env`, single source)

Mandatory:
- `POSTGRES_USER/PASSWORD/DB/HOST/PORT/SCHEMA` — DB connection; the schema (e.g. `rc`) holds all tables.
- `AUTH_SECRET` — HMAC secret for identity signing, **shared by front and back**, ≥32 chars (`openssl rand -hex 32`).
- `BACKEND_PORT` (8000), `FRONT_PORT` (5000), `BACKEND_HOST` (`back`), `BACKEND_HOST_DEV` (`back-dev`) — single sources for ports/hosts.
- `AUTH_APP_ORIGIN` — single source of the app host: Keycloak redirect_uri, Secure cookie, CORS origins and `NEXT_PUBLIC_URL` are derived from it. **No trailing slash!**
- `KEYCLOAK_ENABLED` (`true`/`false`), `AUTH_KEYCLOAK_*` — see `docs/KEYCLOAK.md`.

Optional/derived:
- `VALKEY_URL` — empty = in-memory collaboration (one worker, no cross-worker sync); in the stack `redis://host.docker.internal:6379/0` or the service name.
- `BACKEND_CORS_ORIGINS` — in compose derived from `AUTH_APP_ORIGIN` + localhost; set manually only when running back on the host.
- `BACKEND_INTERNAL_URL` — derived in compose from `BACKEND_HOST/BACKEND_PORT`; mandatory when running front on the host (`front/.env.local`).
- `NEXT_PUBLIC_URL` — **alias of `AUTH_APP_ORIGIN`**, derived automatically in compose, don't set manually.
- `COLLAB_WS_PUBLIC_URL` — empty = the browser connects to its own origin (`/api/ws/` via nginx); set only for a separate WS host (dev without nginx: `ws://127.0.0.1:8000`).
- `AUTH_SESSION_MAX_AGE_SECONDS` — session lifetime, default 43200 (12h), **independent of the short Keycloak ID-token TTL** (otherwise "not authorized").
- `AUTH_SEED_ADMIN_EMAIL` + `AUTH_SEED_ADMIN_PASSWORD` — seed admin (password = hash for local login; the email activates on first Keycloak login).
- Docker build: SSL verification when downloading dependencies is disabled unconditionally (`UV_INSECURE_HOST` / `NODE_TLS_REJECT_UNAUTHORIZED=0`) — for servers behind a corporate proxy.

## Data model (project «Сырьё», key `raw_materials`)

Users live in `app_users`, `projects`, `user_roles` (role `admin`/`asset_worker`, `is_active`), asset assignment — `rm_user_assets` (see `back/src/auth/models.py`).

Domain — `back/src/raw_materials/models.py` (prefix `rm_`):
- `rm_assets` — asset (site); `rm_raw_materials` — raw-material item.
- `rm_asset_raw_materials` — **asset×raw-material link**; this IS the row of the «Движение» and «Цены» forms. FK `ondelete=RESTRICT`: the link can't be deleted while movement/price rows exist.
- `rm_movement_periods` — open report months (PK — `report_month`).
- `rm_material_movements` — «Движение» cells (unique `asset_raw_material_id × report_month`).
- `rm_delivery_bases` — delivery bases (FOB, FCA, …).
- `rm_raw_material_prices` — «Цены» cells (unique by `asset_raw_material_id`); `business_plan_*` — business plan 2026 + service `business_plan_updated_at/by`.
- `rm_report_snapshots` + `rm_snapshot_material_movements` — archive of fixed «Движение» versions (immutable value copies, no FKs to working tables; one snapshot per month: preliminary overwritten, final immutable).

**Canonical cell key** (`back/src/collab/models.py::canonical_key`) — `{table}/{row_id}/{field}` (e.g. `rm_material_movements/{uuid}/demand_plan_tons`); it's also the lease/presence key (`rc:lease:{project}:{key}`, `rc:cellstate:{key}`).

### Form semantics

- **«Движение»** (materialMovement) — per month (room and undo are scoped by `reportMonth`). The «Бизнес-план, т» column is a **projection from «Цены»**, non-editable. Calculated «Отклонение» columns — computed, non-editable.
- **«Цены»** (rawMaterialPrices) — no months; the only place the business plan is edited.
- **Dictionaries** (rawMaterials, assets, deliveryBases, users) — project admin only. Editing assets of a raw-material item = diff link replacement: removed assets deleted, new ones added; if movement/prices exist → a clear business error (409), RESTRICT never leaks out.

## Authentication

Two modes by `KEYCLOAK_ENABLED`:

1. **Keycloak (OIDC):** browser → `/api/auth/login` → authorization code flow (PKCE S256, scopes `openid profile email`) → callback `/api/auth/callback` → `rc_session` cookie (own lifetime `AUTH_SESSION_MAX_AGE_SECONDS`). First login fills/activates the user from Keycloak data (FIO overwritten, email lowercased, lookup case-insensitive). Flow, prod config, claims and provisioning — `docs/KEYCLOAK.md`.
2. **Local login** (`KEYCLOAK_ENABLED=false`): `POST /api/auth/login` (email + password, Argon2), e2e tests use it. Rate limit 20 req/min per IP on `/api/auth/*`.

Session — `rc_session` cookie (JWT-ish, jose; Secure when `https://` is in `AUTH_APP_ORIGIN`). Proxy middleware `front/src/proxy.ts` (matcher `/`, `/blocked` and the project URLs) redirects without the cookie to `/api/auth/login`.

Project URLs — `/raw-materials`, `/renovation`, `/kppk` (root redirects to the first accessible project, preferring renovation). Membership is never created by the root: `GET /api/me` runs with create=False. Opening a project URL calls `POST /api/me/projects/{key}/request-access`, which creates the user record (when missing) and a single inactive project link — the project admin grants access from «Пользователи». Keycloak/callback always return to the app root, so the originally requested path travels in the `rc_auth_return_to` cookie.

**Identity between front and back** — signed headers `X-ReportCollector-*` (email, name, sub, issuer, preferred-username, … + timestamp + HMAC signature over `AUTH_SECRET`): `front/src/shared/auth/identity-headers.ts`, verified in `back/src/auth/service.py::verify_signed_identity`. All `/api/*` front routes (App Router) proxy to back via `front/src/shared/api/backend-proxy.ts` (`/api/raw-materials/[...path]`, `/api/auth/*`, `/api/me`, etc.). The browser can't set custom headers on WebSocket — for WS there's a one-shot **ticket** (signed query parameter): `verify_identity_ticket`.

## API (back, prefix `/api`)

- `auth`: `POST /auth/login`, `POST /auth/users/{id}/password`, `GET/PATCH /me`.
- `raw-materials` (all require a signed identity):
  - Reads: `GET /meta`, `/movement?report_month=`, `/movement/meta`, `/movement/snapshot/{id}`, `/prices`, `/raw-materials`, `/assets`, `/delivery-bases`, `/users`.
  - Edits: `PATCH /movement`, `PATCH /prices` (cell batches → `cell:changed` into the room), dictionary CRUD (admin).
  - Undo/redo: `GET /undo-state?section=&report_month=`, `POST /undo`, `POST /redo` (scope in the body).
  - Fixing: `POST /movement/fix` (admin) — see "Landmines" in AGENTS.md.
  - `POST /export` — Excel (openpyxl): whole/visible selection, for «Движение» — archive versions.
- `ws`: `WS /ws/raw-materials` — collaboration (see `docs/COLLAB.md`).
- Service: `GET /health` (healthcheck), rate-limit and log middlewares in `back/src/main.py`.

Errors: `CellValidationError` → 422, `DictionaryReferenceError` → 409, `ValueError` (not found) → 404, no access → 403. Clear Russian `detail` messages — the front shows them via `readApiDetail` (`front/src/shared/api/raw-materials.ts`).

## Logging

`back/src/app_logging.py`: structured events (`application_started`, `http_request`, `collab.ws_*`, `keycloak_identity_received`, …) to the `LOG_FILE` file and to Loki via Alloy; viewing — Grafana (`{app="reportcollector"}`). No tokens/secrets in logs.

## Deployment (prod)

nginx is **not in Docker**, config `/etc/nginx/conf.d/reportcollector.conf`: `location /` → front `127.0.0.1:5000`; `location /api/ws/` → back `127.0.0.1:8000` with `Upgrade`/`Connection "Upgrade"` and large timeouts (`proxy_read_timeout 86400s`). HTTPS/WSS terminated by nginx (cert in SAN). Prod address: `https://m2v-form-1.h2-group.ru`. Details and WS diagnostics — `docs/COLLAB.md`, "Deployment and diagnostics" section.
