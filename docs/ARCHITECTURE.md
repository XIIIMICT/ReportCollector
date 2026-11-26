# ReportCollector Architecture

Raw-material planning system: Excel-like forms with real-time collaborative editing. Monorepo: `front/` (Next.js), `back/` (FastAPI), `infra/` (compose of external services), `docs/`.

Sections: [System diagram](#system-diagram) · [Services and compose profiles](#services-and-compose-profiles) · [Environment variables](#environment-variables-root-env-single-source) · [Data model «Сырьё»](#data-model-project-сырьё-key-raw_materials) · [Dictionary ordering](#dictionary-ordering) · [Data model «КППК»](#data-model-project-кппк-key-kppk) · [Data model «Логистика»](#data-model-project-логистика-key-logistics) · [Authentication](#authentication) · [API](#api-back-prefix-api) · [Logging](#logging) · [Deployment](#deployment-prod) · [Plan import](#renovation-plan-import-technical-directorate-xlsb--xlsx) · [Export jobs](#renovation-plan-export-jobs) · [Transactional collaboration](#transactional-collaboration)

## Ordered column filters

- Header filters retain application order in per-table local storage. Editing
  preserves priority; removing and reapplying appends a filter. Header badges
  and the numbered status list show this order. A column's facet options use
  only preceding filters; all active filters combine with AND for rows/totals.
- Renovation sends header constraints, date zones and facet search in JSON to
  `POST /plan/rows/query`, `/plan/summary/query` and `/plan/facets/query`.
  Ordered plan-column sorting is sent in the same rows/export JSON contract;
  PostgreSQL orders the complete filtered scope before the virtualized window.
  Toolbar scope stays in the URL. Legacy GET reads remain available without
  header constraints; a `filters` query parameter is rejected with 422.
- Shared `DataGrid` suppresses index-based merges whenever filtering or sorting
  is active, including restored state. KPPK supplies the original merge ranges.
  Stacked toolbar rows keep a 0.75rem gap at every viewport size while visible.

## System diagram

```
Browser ── https://<host> ──► nginx (central infra stack, on prod)
                              ├── /              → front (Next.js standalone, :5000)
                              └── /api/ws/       → back (FastAPI/Granian, :8000)
                              (HTTPS + WSS terminated in nginx)
front ── BACKEND_INTERNAL_URL ──► back (/api/*, server-side, signed headers)
back  ── Postgres (asyncpg, schema from POSTGRES_SCHEMA)
     ── Valkey/Redis (leases, presence, pub/sub; VALKEY_URL empty → in-memory)
     ── Loki ← Alloy (logs; Grafana for viewing)
Keycloak ── OIDC (login; local container or corporate)
```

## Services and compose profiles

`compose.yaml` (name: `reportcollector`) `include`s four infra files in `ReportCollector/infra/`: `compose.db.yaml` (postgres + valkey, no profile), `compose.auth.yaml` (keycloak, no profile), `compose.monitoring.yaml` (alloy without a profile + loki/grafana/prometheus/alertmanager, no profile), and `compose.proxy.yaml` (nginx, no profile; raises separately — `just proxy-stand`).

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `rc-back` / `rc-back-dev` | prod / dev | FastAPI, Granian, port `BACKEND_PORT` |
| `front` / `front-dev` | `rc-front` / `rc-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload) |
| postgres, valkey | `rc-postgres` / `rc-valkey` | — | data services (`just db-stand`) |
| keycloak | `rc-keycloak` | — | auth service (`just auth-stand`) |
| alloy | `rc-alloy` | — | log collector (also starts with the full stack; `just monitoring` targets an external Loki) |
| loki, grafana, prometheus, alertmanager | — | — | logs + metrics (`just monitoring-stand`) |
| nginx | `rc-nginx` | — | reverse proxy (`just proxy-stand`) |

Key point: **ports and hosts are set once in the root `.env`** and spread into the Dockerfiles (build-arg `PORT`/`BACKEND_PORT`), port publishing, healthchecks, and internal URLs. All mandatory (`:?` in compose — fails with a clear error instead of staying silent). No defaults in Dockerfiles.

### Two deployment modes

**Local standalone** (developer machine, one compose project): `just db-stand` / `just auth-stand` / `just monitoring-stand` / `just proxy-stand` bring up postgres + valkey, Keycloak, the whole observability stack (Loki, Grafana, Alloy, Prometheus, Alertmanager) and a reverse proxy (nginx, `fholzer/nginx-brotli` build — same files as the central infra stack, `infra/check-drift.py` verifies the copies don't drift). None of these services carries a profile: `just prod` / `just dev` name the app services explicitly (`back front` / `back-dev front-dev`), so they never start the local infrastructure by accident. `just monitoring` starts Alloy alone against an external Loki. Service-addressing conventions inside the local stack: `LOKI_URL=http://loki:3100` (service name within the same compose project) and the environment label comes from `LOG_ENV=local` in `.env`.

**Central prod stack** (DWH infra/, a separate compose project, possibly another host): Loki + Grafana + Prometheus + Alertmanager + Pushgateway + nginx (brotli) live there and serve ALL apps; the app repo brings only stateless collectors. ReportCollector connects to the shared backends via `.env` values — `LOKI_URL` (the app's Alloy pushes there), and Prometheus in the central stack scrapes the backend through `host.docker.internal:<BACKEND_PORT>`; its config does not expand compose `${VAR}` (files: `infra/prometheus/prometheus.yml`). The app alert rules belong to the stack that scrapes — in prod they go into the central `infra/prometheus/rules/`. The label contract that keeps both apps distinguishable in the shared stack: `app=reportcollector|excel_loader`, `env=<LOG_ENV>`, `job=reportcollector-back` (the scrape job name).

## Environment variables (root `.env`, single source)

Mandatory:
- `POSTGRES_USER/PASSWORD/DB/HOST/PORT/SCHEMA` — DB connection; the schema (e.g. `rc`) holds all tables.
- `AUTH_SECRET` — HMAC secret for identity signing, **shared by front and back**, ≥32 chars (`openssl rand -hex 32`).
- `BACKEND_PORT` (8000), `FRONT_PORT` (5000), `BACKEND_HOST` (`back`), `BACKEND_HOST_DEV` (`back-dev`) — single sources for ports/hosts.
- `AUTH_APP_ORIGIN` — single source of the app host: Keycloak redirect_uri, Secure cookie, CORS origins and `NEXT_PUBLIC_URL` are derived from it. **No trailing slash!**
- `KEYCLOAK_ENABLED` (`true`/`false`), `AUTH_KEYCLOAK_*` — see `docs/KEYCLOAK.md`.

Optional/derived:
- `VALKEY_URL` — empty = in-memory collaboration (one worker, no cross-worker sync); in the stack `redis://host.docker.internal:6379/0` or the service name.
- `LOG_ENV` (`local`/`prod`) — the `env` label Alloy stamps onto every log line; `local` by default, set `prod` in a deployed `.env`.
- `LOKI_URL` — the Loki the app's Alloy pushes to (`http://loki:3100` resolves inside the local compose project; a central/external Loki is `host.docker.internal:3100` or the server address).
- `BACKEND_CORS_ORIGINS` — in compose derived from `AUTH_APP_ORIGIN` + localhost; set manually only when running back on the host.
- `BACKEND_INTERNAL_URL` — derived in compose from `BACKEND_HOST/BACKEND_PORT`; mandatory when running front on the host (`front/.env.local`).
- `NEXT_PUBLIC_URL` — **alias of `AUTH_APP_ORIGIN`**, derived automatically in compose, don't set manually.
- `COLLAB_WS_PUBLIC_URL` — empty = the browser connects to its own origin (`/api/ws/` via nginx); set only for a separate WS host (dev without nginx: `ws://127.0.0.1:8000`).
- `SUPPORT_MAIL` — the mailbox behind the header's «Связаться с разработчиком» control (the subject names the project: «Обращение по РепортКоллектор Ремонт / КППК / Сырьё»). Read **server-side** (`getSupportMail` in `front/src/shared/auth/session.ts`) and passed into the client as a prop, so it is not a `NEXT_PUBLIC_` variable and never has to reach the bundle; empty = the control is not rendered. The same control is rendered on the «нет доступа к проекту» page, always expanded.
- `AUTH_SESSION_MAX_AGE_SECONDS` — session lifetime, default **34560000** (400 days — the practical maximum a browser keeps a persistent cookie), **independent of the short Keycloak ID-token TTL and of the Keycloak SSO session** (nothing re-validates the cookie, so a stand restart or an expired corporate SSO does not sign the user out).
- Session cookies require a valid HMAC, exactly two token segments, nonempty subject/issuer and a finite unexpired numeric timestamp; malformed signed payloads are rejected too.
- `AUTH_SEED_ADMIN_EMAIL` + `AUTH_SEED_ADMIN_PASSWORD` — seed admin (password = hash for local login; the email activates on first Keycloak login).
- Corporate-proxy compatibility is retained: backend dependency downloads allow insecure `pypi.org`/`files.pythonhosted.org`; frontend Bun build/dev stages use `NODE_TLS_REJECT_UNAUTHORIZED=0`. This is an explicit certificate-verification exception, not a security best practice. It is not inherited by the Node production runtime. Prefer a trusted corporate CA when available; do not remove the existing workaround without testing downloads behind the proxy.
- Database migrations run from the release checkout on the host/CI with uv, not inside runtime images. See `docs/WORKFLOW.md` for the one-time guarded adoption of `0001_baseline` by existing installations.

## Data model (project «Сырьё», key `raw_materials`)

Users live in `app_users`, `projects`, `user_roles` (role `admin` / `reviewer` / `asset_worker` / `blocked`, `is_active` derived from the role), asset assignment — `rm_user_assets` (see `back/src/auth/models.py`).

Domain — `back/src/raw_materials/models.py` (prefix `rm_`):
- `rm_assets` — asset (site); `rm_raw_materials` — raw-material item.
- `rm_asset_raw_materials` — **asset×raw-material link**; this IS the row of the «Движение» and «Цены» forms. FK `ondelete=RESTRICT`: the link can't be deleted while movement/price rows exist.
- `rm_movement_periods` — open report months (PK — `report_month`).
- `rm_material_movements` — «Движение» cells (unique `asset_raw_material_id × report_month`).
- `rm_delivery_bases` — delivery bases (FOB, FCA, …).
- `rm_raw_material_prices` — «Цены» cells (unique by `asset_raw_material_id`); `business_plan_*` — business plan 2026 + service `business_plan_updated_at/by`.
- `rm_report_snapshots` + `rm_snapshot_material_movements` — archive of fixed «Движение» versions (immutable value copies, no FKs to working tables; one snapshot per month: preliminary overwritten, final immutable).

**Canonical cell key** (`back/src/collab/models.py::canonical_key`) — `{table}/{row_id}/{field}` (e.g. `rm_material_movements/{uuid}/demandPlanTons`); it's also the lease/presence key (`rc:lease:{project}:{key}`, PostgreSQL `collab_cell_states.key`).

### Form semantics

- **«Движение»** (materialMovement) — per month (room and undo are scoped by `reportMonth`). The «Бизнес-план, т» column is a **projection from «Цены»**, non-editable. Calculated «Отклонение» columns — computed, non-editable.
- **«Цены»** (rawMaterialPrices) — no months; the only place the business plan is edited.
- **Dictionaries** (rawMaterials, assets, deliveryBases, users) — project admin only. Editing assets of a raw-material item = diff link replacement: removed assets deleted, new ones added; if movement/prices exist → a clear business error (409), RESTRICT never leaks out.

### Data catalog comments

Migration `0014_data_catalog_comments` documents every application-owned table
and column in Russian (30 tables, 495 columns); `0015_updater_user_catalog`
refines the comments and replaces `updated_by` with nullable
`updater_user_id` foreign keys to `app_users.id` in 16 application tables.
The old text values are intentionally discarded. The API still exposes
`updatedBy` as the referenced user's current name (or email when unnamed).
Columns `id`, `created_at` and `updated_at` have no comments; foreign-key
comments use `ID`, and catalog comments have no trailing period. Renovation plan column comments
reuse the complete normalized header hierarchy shown by the form selection
status. Source schemas `raw.*`/`dict.*` and `alembic_version` are excluded.
`back/src/common/data_catalog.py` also attaches comments when each model module
registers its tables, including tables recreated by the KPPK seeder. The
migration carries a frozen snapshot, not runtime imports; future comment
changes need a new migration. Legacy baseline adoption tolerates absent
catalog comments deferred to 0014 and the known audit-column transition deferred
to 0015, but still rejects unexpected comments and structural drift. Legacy KPPK fields not exposed by the form
retain source-field descriptions; undocumented units are not inferred.

## Dictionary ordering

The «Ремонт» reference catalogs (категория работ, вид работ, отнесение затрат,
способ выполнения, группа критичности) are dictionaries, not source-ordered
lists: `r_reference_values` is read and shown **alphabetically by `label`**
(`0019_drop_reference_sort_order` removed the stored `sort_order` — it only
reproduced the Excel row order and made the lists look unordered). The same
order feeds the catalog grid and the form's dropdown options. Adding an option
no longer invents a position.

The «Сырьё» dictionaries already sort by name and have no order column. КППК
keeps its source numbering («№» — `product_num` / `raw_num`) as the primary
sort key — that ordering is part of the source data and stays.

The catalog row id is the option's identity: `value_key` was removed in
`0021_drop_reference_value_key` (nothing referenced it — plan cells store the
UUID). Seeded options get a deterministic id from `reference_option_id()`
(`uuid5` over `(catalog, label)`), so a re-seed recognises its own rows.

`r_reference_values.deleted_at` is the forward-compatible soft-delete marker:
NULL means "not deleted", a timestamp is the moment of deletion. **Deletes are
still hard**, so nothing writes it yet — the column is prepared ahead of the
feature (`0022_reference_payload_to_deleted_at`, which also dropped the unused
`payload` placeholder).


## Topic documents

- `docs/DATA-JOBS.md` — renovation import/preflight, durable jobs and XLSX export.
- `docs/RENOVATION-DESIGN.md` — renovation service/router/API module boundaries and measured query cost.
- `docs/DOMAINS.md` — КППК and Логистика data models.

## Authentication

Two modes by `KEYCLOAK_ENABLED`:

1. **Keycloak (OIDC):** browser → `/api/auth/login` → authorization code flow (PKCE S256, scopes `openid profile email`) → callback `/api/auth/callback` → `rc_session` cookie (own lifetime `AUTH_SESSION_MAX_AGE_SECONDS`). First login fills/activates the user from Keycloak data (FIO overwritten, email lowercased, lookup case-insensitive). Flow, prod config, claims and provisioning — `docs/KEYCLOAK.md`.
2. **Local login** (`KEYCLOAK_ENABLED=false`): `POST /api/auth/login` (email + password, Argon2), e2e tests use it. Rate limit 20 req/min per IP on `/api/auth/*`.

Session — `rc_session` cookie (JWT-ish, jose; Secure when `https://` is in `AUTH_APP_ORIGIN`). Proxy middleware `front/src/proxy.ts` (matcher `/`, `/blocked` and the project URLs) redirects without the cookie to `/api/auth/login`.

Project URLs — `/raw-materials`, `/renovation`, `/kppk`, `/logistics` (root redirects to the first accessible project, preferring renovation). Membership is never created by the root: `GET /api/me` runs with create=False. Opening a project URL calls `POST /api/me/projects/{key}/request-access`, which creates the user record (when missing) and a single inactive project link — the project admin grants access from «Пользователи». Keycloak/callback always return to the app root, so the originally requested path travels in the `rc_auth_return_to` cookie.

**The active form is URL state.** `?section=<form id>` on a project URL pins the open form, so a reload (and browser back/forward) lands on it instead of the default. The server page `front/src/app/[project]/page.tsx` reads `searchParams.section` and passes it as `initialSection`; each workspace restores it (renovation: `plan` / `assets` / a catalog id / `users`; raw materials: `movement:{period}` / `rawMaterialPrices` / a dictionary; kppk: a dictionary or `kppkRealization`), validates it against its own tabs and falls back to the first one when unknown (notifying the URL so a stale value is corrected). A change is mirrored back with `router.replace(..., { scroll: false })` — `replace`, not `push`, keeps the back button on the previous page. Switching project (`router.push(slug)`) drops the parameter, since form ids are project-specific.

**Panel filters travel with the tab.** A workspace whose toolbar has a filter panel (a row beside the tabs or under them — `ToolbarFilters`) keeps those values in the URL too, scoped to the tab that owns them: `?section=kppkRealization&f.kppkRealization.org=<id>&f.kppkRealization.year=2027`. The shared `useToolbarFilters(tabId)` (`front/src/shared/ui/use-toolbar-filters.ts`) reads them after hydration (SSR renders "no filters", like every restored state) and `setFilter(tab, name, value)` writes one — a scalar as itself, a multi-select as a JSON list, and **`null`/`[]` deletes the parameter** so "no constraint" has one representation. Switching tabs touches only the new tab's own keys, so returning to a tab restores what the user left there. A panel value is validated by its owner on restore (e.g. the plan's block filter drops a value the plan no longer has). Client-grid column filters and sorting are **not** URL state — they belong to the grid. The renovation plan persists header filters and sorting in the same localStorage namespaces as the other tables; its ordered sort levels are sent in the JSON row/export contract and ordered by PostgreSQL because Infinity Scroll holds only a sparse window.

**Restoring must not validate against options that have not loaded.** A restored value is only "stale" once the list it must belong to is KNOWN, and those lists arrive with a later request. `restoredFilterVerdict(stored, options)` encodes the three states, and every owner uses it instead of `options.includes(value)`:

- `options === null` → **pending**: keep the value and do **not** rewrite the URL; re-evaluate when the options load;
- `"keep"` → the value is one of the options (or nothing is stored);
- `"clear"` → the options are loaded and the value is gone: drop it, never leave a filter that hides every row.

`[]` and `null` are deliberately different: `null` is "not loaded", `[]` is "loaded and empty". A naive guard that treats the empty first-render list as final deleted a perfectly valid «Блок дирекции = МТО» from the URL on every reload of the plan, and reset the raw-materials archive version the same way. The archive version is a panel filter too (`f.materialMovement.snapshot`): it survives a reload and is re-opened once restored, because the picker alone would show it selected while the grid still held the working rows.

Nothing else is persisted: the selected version, the report month and the grid's column widths are not saved.


**Identity between front and back** — signed headers `X-ReportCollector-*` (email, name, sub, issuer, preferred-username, … + timestamp + HMAC signature over `AUTH_SECRET`): `front/src/shared/auth/identity-headers.ts`, verified in `back/src/auth/service.py::verify_signed_identity`. All `/api/*` front routes (App Router) proxy to back via `front/src/shared/api/backend-proxy.ts` (`/api/raw-materials/[...path]`, `/api/auth/*`, `/api/me`, etc.). The browser can't set custom headers on WebSocket — for WS there's a one-shot **ticket** (signed query parameter): `verify_identity_ticket`.

## API (back, prefix `/api`)

- `auth`: `POST /auth/login`, `POST /auth/users/{id}/password`, `GET/PATCH /me`.
- `raw-materials` (all require a signed identity):
  - Reads: `GET /meta`, `/movement?report_month=`, `/movement/meta`, `/movement/snapshot/{id}`, `/prices`, `/raw-materials`, `/assets`, `/delivery-bases`, `/users`.
  - Edits: `PATCH /movement`, `PATCH /prices` (cell batches → `cell:changed` into the room), dictionary CRUD (admin). A «Пользователи» membership is a `role` (`admin` | `asset_worker` | `blocked`); the old `isAdmin`/`isActive` fields still write (and are derived from the role), `blocked` keeps the member out, and an asset worker without a single grant is denied the project outright.
  - Undo/redo: `GET /undo-state?section=&report_month=`, `POST /undo`, `POST /redo` (scope in the body).
  - Fixing: `POST /movement/fix` (admin) — see "Landmines" in AGENTS.md.
  - `POST /export` — Excel (openpyxl): whole/visible selection, for «Движение» — archive versions.
  - Renovation plan export — **background jobs**: `POST /plan/export/jobs` (202 + job id), `GET /plan/export/jobs/{id}` (status/progress), `GET /plan/export/jobs/{id}/file` (download). The legacy `GET/POST /plan/export` endpoints remain for compatibility: bounded requests render inline, while larger scopes are admitted to the same durable job boundary.
  - Renovation plan **import preflight** (admin): `POST /plan/import/inspect` (multipart `file`, `.xlsb` or `.xlsx`) validates the technical-directorate workbook **without writing** to the database. Readiness and the response contract — see «Renovation plan import» below.
  - Renovation plan **import load** (admin): `POST /plan/import/apply` (multipart `file`, `.xlsb` or `.xlsx`) stages the checked workbook and returns `202` with a durable job. Poll `GET /plan/import/jobs/{id}` (or cancel with `DELETE`) while a worker parses and writes the workbook under the project gate. See `docs/DATA-JOBS.md`, «Loading the checked file».
- `kppk` (signed identity; reads for every project member, mutations admin-only): dictionary CRUD — `GET/POST/PATCH/DELETE /product-positions[/{id}]` (PATCH `{field, value}`, `field` = `name` | `orgNames` | `productNum`; create accepts `productNum`; delete removes the product with its links), `/raws` (PATCH `{field, value}` for `name`/`rawNum`), `/orgs` (PATCH `{field, value}` for `code`/`name`), `/periods` (PATCH `{field, value}`; no UI in this phase). The «Пользователи» dictionary takes a `role` (`admin` | `blocked`; the access flag is derived, a blocked member can be deleted). The source number («№», `productNum`/`rawNum`) is editable and unique — an occupied number is rejected with a clear `400`; `null` stands for a row added by hand (several NULLs are allowed); positions/raws are returned in the source-number order (unnumbered rows last). The «ДЗО» list is ordered by the full name and exposes no number. Undo/redo join the shared collaboration journal: every mutation carries the page-lifetime `session_id` (query) and is journaled as one action scoped to the tab (`scope = f"{section}/"`, no report month; `collab/undo.py` dispatches `kppk_products`/`kppk_raws`/`kppk_orgs` to `apply_kppk_cell_value`) — `GET /undo-state?section=&session_id=`, `POST /undo`, `POST /redo` (body `{skipActionId, section}`, admin; no WebSocket room and no `cell:changed` — the front refreshes the grid itself after undo/redo). `POST /export` streams the tab as XLSX (whole/visible via `rowIds`, `columns` from the grid). Deletes follow RESTRICT: a ДЗО/period referenced by the fact/plan copies is rejected with a clear `400`.
- `logistics` (signed identity, role-based): read-only dictionaries — `GET /dictionaries/countries`, `/dictionaries/transport-types`, `/dictionaries/orgs`, and the application-owned `/dictionaries/hazard-classes` (1..9, no tab yet) — open to every member (the rates form resolves its labels there). The external dictionaries return `{source: "external" | "fallback", rows: [...]}`; there are no write endpoints for them, because the `dict` schema belongs to another system. The project members dictionary is editable and admin-only: `GET/POST /users`, `PATCH/DELETE /users/{id}?session_id=` (journaled as role/grant snapshots), plus `GET /users/undo-state` and `POST /users/undo|/redo` — a membership is a `role` (`admin` | `reviewer` | `asset_worker` | `blocked`) plus, for a worker, `assetIds`/`assetNames`; a blocked member can be deleted, and nobody can edit/delete themselves. The main «Ставки» table: `GET /rates?month=&year=` (the period view: `{periodDate, usdRate, rows}` with the computed direction/dangerous/dollar/deviation columns; an asset worker gets only their assets' rows), `POST /rates?session_id=` (a complete row; an omitted `assetId` is stamped with the worker's granted asset), `PATCH /rates/{id}?session_id=` (`{field, value}`; a worker is limited to their assets and gets `404` elsewhere), `DELETE /rates/{id}?session_id=` (administrator only; journaled so undo restores the row), `GET /rates/undo-state?session_id=&period_date=`, `POST /rates/undo|/redo` (body `{skipActionId, section, reportMonth}`, scope `logisticsRates/<period>`; a worker replays only their own assets' actions). A reviewer can read the table and never writes (`403`). Collaboration room `WS /ws/logistics` (any working member). Excel export: `POST /export` (inline XLSX through the shared renderer; the rates require `month`/`year` and are scoped by role, «Пользователи» is admin-only, the dictionaries are open to members).
- `ws`: `WS /ws/raw-materials` — collaboration (see `docs/COLLAB.md`).
- Service: `GET /health` (process liveness), `GET /ready` (PostgreSQL/Valkey readiness, including the required outbox migration), rate-limit and log middlewares in `back/src/main.py`. HTTP responses carry `X-Request-ID`, also recorded in structured request logs. Granian access/handshake logs redact credential query parameters (`ticket`, `access_token`, `token`); the external reverse proxy must apply its own redaction policy.

Errors: `CellValidationError` → 422, `DictionaryReferenceError` → 409, `ValueError` (not found) → 404, no access → 403. Clear Russian `detail` messages — the front shows them via `readApiDetail` (`front/src/shared/api/raw-materials.ts`).

## Logging

`back/src/app_logging.py`: structured events (`application_started`, `http_request`, `collab.ws_*`, `keycloak_identity_received`, …) to the `LOG_FILE` file and to Loki via Alloy; viewing — Grafana (`{app="reportcollector"}`). No tokens/secrets in logs. The Alloy config stamps every line with `app=reportcollector`, `job=reportcollector` and `env=<LOG_ENV>` from `compose`/`.env` (`local` by default; set `prod` on the server).

**Log-field contract.** `JsonFormatter` serializes only the bounded `SAFE_LOG_FIELDS` allowlist: `event`, `request_id`, `job_id`, `operation`, `phase`, `outcome`, `error_code`, `error_type`, counts/durations/sizes, route template, status, `project_key` and `room`. Identity claims (email, display/given/family name, OIDC `sub`, issuer, `preferred_username`, `email_verified`), raw request values (client id, `Origin`) and free-form exception text (`error=`) are never emitted — even if a caller passes them. Failure events carry a stable `error_code` and `error_type` (`type(error).__name__`), not `str(error)`: exception messages can embed workbook cells, SQL parameters or keys. The auth events (`keycloak_identity_received`, `user_created`, `blocked_user_attempt`, `local_login_*`) and `collab.ws_*` events therefore contain no identity values; correlate them through the enclosing request id. Regression sentinels live in `back/tests/test_log_privacy.py`. This is a log-content contract only: metrics, topology and dashboards are unchanged.

The file rotates (`RotatingFileHandler`, 64 MB × 5 backups — at most ~320 MB; a plain handler reached 174 MB in 33 hours). Rotation **renames** the active file, so Alloy's path-based tail re-reads the new one on its 5 s `sync_period`; a handful of lines can be missed at a rollover, which is the accepted price of a bounded disk. Loki retention is `LOKI_RETENTION=168h` (7 days) rendered by `-config.expand-env=true`, plus the compactor that actually enforces it — without both, retention is inert and chunks are never deleted. Prometheus retention is `PROMETHEUS_RETENTION=15d` (container flag); both defaults are identical in all projects and the central stack and declared in `.env.example`.

`request_id` is recorded by the middleware, returned to the client in `X-Request-ID`, and ingested as Loki **structured metadata** (not a label — a uuid per request would explode the index). Trace one request end to end with `{app="reportcollector"} | request_id="<id>"`.

## Monitoring

Logs answer "what exactly happened"; metrics answer "how many and how often". **Percentiles exist only in metrics** — they are computed from histogram buckets at write time and cannot be derived from log lines.

- `back/src/metrics.py` — a dedicated `CollectorRegistry`; `/metrics` (unauthenticated, aggregate values only) is scraped by Prometheus.
- HTTP: `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` (buckets 5 ms…30 s), `http_requests_in_progress`. The `route` label is the **route template** (`/kppk/product-positions/{position_id}`), never the raw path — otherwise every row id would create its own time series; unmatched paths share `route="unmatched"`. `/health` and `/ready` are skipped in the LOG but still measured.
- Collaboration: `ws_connections_active`, `ws_rooms_active`, `ws_events_relayed_total{room_section}`, `ws_slow_consumers_total{room_section}`. The last one matters most at scale: a client whose queue fills is **disconnected on purpose** and reconnects silently, so nothing errors — only this counter reveals it. `room_section` drops the month and the ДЗО scope, keeping one series per form.
- Authentication: `auth_rate_limit_events_total{storage,outcome}` records shared Valkey, local fallback, allowed and rejected login-window decisions. Argon2 password work uses a dedicated bounded executor so expensive hashing does not occupy the ASGI event loop.
- Dashboards (`infra/grafana/provisioning/dashboards-json/`): **Сервис** (HTTP percentiles, database pool/query/lock pressure, import/export phases and queue, Valkey/dependency health, RSS/event loop/disk), **Сотрудничество** (WS load, slow consumers), **Ошибки** (Loki, with `request_id` correlation). Provisioned from git, `allowUiUpdates: false` — edit the JSON, not the browser.
- **Port scheme (G2):** the project stacks and the central DWH stack all publish 3000/3100/9090/9091/9093 (Alloy 12345). Only one full stand runs on a machine at a time; remap the host ports if two are needed side by side. External mode is pure env override (`LOKI_URL`, `BACKEND_METRICS_HOST`/`BACKEND_PORT`, `METRICS_PUSHGATEWAY_URL` where a batch client exists).
- Prometheus scrapes `<BACKEND_METRICS_HOST>:<BACKEND_PORT>` (15 s, configurable 15-day retention). Prometheus does not expand compose variables, so `infra/prometheus/prometheus.yml.tpl` is rendered at container start by `entrypoint.sh` into `/tmp/prometheus.yml` (the same pattern as Alertmanager); the old hardcoded `prometheus.yml` was removed. The target also appears in the central DWH stack, where the same rendering contract applies.
- **Alerts** (`infra/prometheus/alert-rules.yml`, 12 rules): a 5xx burst (>0.05/s for 2 min), an immediate recent server/interrupted-response failure, accumulating 5xx (>20/h), `up == 0`, collaboration slow consumers, pool exhaustion, query timeout/deadlock failures, export failures, event-loop lag, disk pressure, readiness dependency failures and Valkey subscription failures. The immediate rule ignores the initial zero gauge and expires five minutes after the last failure. The event-loop/disk/capacity values are operational guardrails; load-based latency and memory thresholds remain to be calibrated. Alertmanager groups them (`group_wait 30s`, repeat 4h) and exposes them at `http://localhost:9093`.
- **Stack self-monitoring:** the four `ReportCollector{Loki,Alertmanager,Grafana,PrometheusSelfScrape}...` rules carry the full `up` set for every component this stack runs. The project prefix is required because project rule files are copied into the central DWH `rules/` directory.
- **Mail delivery — canonical description for this repo;** the other docs and `.env.example` point here. Alertmanager sends plain SMTP to the local `smtp-relay` (`juanluisbaptiste/postfix:1.11`) on port 25 inside the private compose network; the relay authenticates to the corporate SMTP over STARTTLS 587. `smtp_require_tls: false` is a **documented deviation limited to the internal hop**; port 465 (implicit TLS) remains incompatible. `SMTP_RELAY_*` configures the uplink, `ALERT_SMTP_*` the envelope, and `ALERT_SMTP_USER` must still equal `ALERT_SMTP_FROM`. `entrypoint.sh` renders the template at container start — editing `.env` needs `just monitoring-stand`. An earlier direct-to-corporate-SMTP delivery (2026-10-27) was verified, but later logs showed `connection refused`; re-verify after deploying the relay. Remaining alert and load work is tracked in `docs/TODO.md`.
- Remaining load, scaling and operational work — `docs/TODO.md`.

## Deployment (prod)

The reverse proxy is part of the central DWH infra stack now (`infra/compose.proxy.yaml`, image `fholzer/nginx-brotli`): its template does `location /` → `NGINX_UPSTREAM_HOST:NGINX_UPSTREAM_PORT` (front, defaults `host.docker.internal:5000`) and `location /api/ws/` → `NGINX_BACKEND_HOST:NGINX_BACKEND_PORT` (back, defaults `host.docker.internal:8000`, the old static `back` default never resolved from the central stack's own project). Upstream hostnames are resolved per request (resolver `127.0.0.11` + `set` variables), so nginx also starts before the app containers do. HTTPS/WSS is terminated in nginx (host certs via `NGINX_TLS_CERT_PATH`/`NGINX_TLS_KEY_PATH`, an empty pair falls back to a generated self-signed certificate). The standalone build of the same proxy lives in `ReportCollector/infra/nginx` (`just proxy-stand`); the two `infra/nginx` trees must stay identical — `infra/check-drift.py` (DWH checkout) fails on drift. Prod address: `https://m2v-form-1.h2-group.ru`. Details and WS diagnostics — `docs/COLLAB.md`, section "Deployment and WS diagnostics". The host-nginx configuration below remains for hosts where the proxy is deliberately NOT containerized.

**Bind-folder ownership on Linux.** `./logs` (written by both app containers)
belongs on the host to the account that deploys the repo, so images' built-in
`appuser` (uid 100) hits «Permission denied» on it. `compose.yaml` runs
`back`/`front` (and their dev variants) as
`user: "${HOST_UID:+${HOST_UID}:${HOST_GID}}"` with `HOST_UID`/`HOST_GID` from
`.env` (`id -u` / `id -g`) — set **both or neither**. The `:+` form is
load-bearing: when the variables are unset it drops the `user:` key entirely, so
the container runs as its image user (back `appuser` 100:101, front root) — what
Windows and Docker Desktop need. A plain `${HOST_UID:-}:${HOST_GID:-}`
substitutes the literal `:` and compose rejects it, while a `:-1000` default is
applied even on Docker Desktop (the directive does take effect there, it is just
invisible in Windows ACLs) and broke `just dev` outright: uid 1000 could neither
append `/app/logs/reportcollector.log` (`PermissionError`) nor create
`/app/.next` (`EACCES`), so both dev containers exited 1. Fix legacy root-owned
log files once with `sudo chown -R "$(id -u):$(id -g)" logs` — do not reach for
777.

### Compression (must be on in nginx)

The bundle is 3.4 MB of JavaScript; without compression the browser downloads all of it. Measured on the three largest chunks alone (2 544 KB): **→ 807 KB with gzip (−68%)**. Across all eight chunks above 50 KB (2 966 KB): **952 KB at level 5**. The per-level numbers are in the config comment below.

Two separate gaps, both confirmed by `curl -sI -H 'Accept-Encoding: gzip'`:

- **HTML** is served uncompressed even with `compress: true` in `next.config.ts` — the standalone server compresses the static chunks, not the document. Only nginx can fix it.
- **The static chunks** were uncompressed on prod because nginx did not compress them.

Add to the `http {}` block of `/etc/nginx/nginx.conf` (or at the top of the site file):

```nginx
# gzip: the baseline every browser supports.
gzip on;
# Measured on the 8 chunks above 50 KB (2 966 KB raw): level 1 → 1 338 KB,
# level 5 → 952 KB, level 9 → 939 KB. Level 9 buys 14 KB (1.5%) over 5 for a
# multiple of the CPU, so 5 is the point of the curve here.
gzip_comp_level 5;
# Only 1 of the 25 built chunks is under 1 KB, so this barely matters for the
# bundle — it matters for small HTML/JSON responses, where gzip's headers can
# outweigh the saving.
gzip_min_length 1024;
gzip_vary on;                   # correct caching for shared proxies
gzip_proxied any;
gzip_types
    text/plain text/css text/xml
    application/javascript application/json application/xml
    application/rss+xml application/manifest+json
    image/svg+xml;
# `text/html` is ALWAYS compressed by gzip on — listing it is a no-op; not listing it does not disable it.
```

Verify after reload — every command must print a `Content-Encoding` header:

```bash
curl -sI -H 'Accept-Encoding: gzip' https://<host>/                     | grep -i content-encoding
curl -sI -H 'Accept-Encoding: gzip' https://<host>/_next/static/chunks/<any>.js | grep -i content-encoding
```

**Optional — brotli** adds **8.9% over gzip 9** on the same chunks (939 KB → 855 KB; measured, brotli quality 5), and every current browser accepts it. Less than the "15–20%" folklore suggests for this bundle — worth having, not worth a migration risk. Needs `ngx_brotli` (the official `nginx` package does not ship it; Debian/Ubuntu have `libnginx-mod-brotli`):

```nginx
brotli on;
brotli_comp_level 5;
brotli_min_length 1024;
brotli_types text/plain text/css application/javascript application/json image/svg+xml;
```

Left uncompressed, the first load pays ~1.7 MB of avoidable transfer per visitor, on every cache miss.

**API JSON is not compressed by the Next proxy.** Its former manual gzip broke on Next 16.4.5: `sendResponse` drops `Content-Encoding` from route-handler responses (it assumes a fetch-decompressed body), so the browser received gzip bytes labelled `application/json` and every workspace stayed on its loading skeleton. Compression belongs entirely to nginx: `location /` sends `Accept-Encoding: ""` upstream, the proxy returns the backend's plain JSON, and nginx gzips `application/json` (already in `gzip_types`). In dev, where the browser talks to Next directly, API JSON arrives uncompressed — a few MB over localhost traded for a response the browser can parse.

### Upload proxying: bounded buffering is deliberate

`front/src/shared/api/backend-proxy.ts` still reads a multipart upload into a
single Node `Buffer` before forwarding it (`request.arrayBuffer()`). The
original `Content-Type`, including the boundary, is preserved byte-for-byte and
the **response** body is streamed. Re-streaming `request.body` into undici with
`duplex: "half"` was tried and dropped: in the Next dev route handler the parts
were lost, so the backend received an incomplete multipart body. The installed
Next 16.4.5 documents streaming **responses** but offers no supported contract
for streaming an incoming route-handler body into `fetch`; the request is
already bounded to 256 MiB by `ImportUploadLimitsMiddleware` and the spooled
`UploadFile`, so the buffered hop is a known, bounded cost rather than an
unbounded one. Owner options if this hop becomes the bottleneck: (a) upload
straight to the backend with CORS and signed identity headers, bypassing Next;
(b) terminate the proxy in the central nginx stack (deferred with infra work);
(c) add a chunked/direct-to-object-storage upload API. Do not switch to a
fragile stream forward without verifying the full multipart payload on the real
Next runtime — the deferred upload-streaming item in `docs/TODO.md` explicitly requires that.

### Bundle shape (why the first load is heavy)

`ReportWorkspace` used to import all three project workspaces statically, so opening «Движение» also downloaded the КППК and renovation grids — and a grid is `@revolist/react-datagrid`, the bulk of the largest chunk. They are now `next/dynamic`, one per URL: the largest chunk went **2 179 KB → 1 459 KB**, and the grid split into its own 746 KB chunk that only the project that needs it loads. Adding a new project to `report-workspace.tsx` means adding another `dynamic(...)` entry, not a static import.

## Implemented invariants

These are current guarantees, not planned work:

- **PostgreSQL is the source of truth** for business data, edit stacks, cell
  states and the outbox; a mutation and its history/notifications commit in one
  transaction (`database.py` request dependency).
- **Valkey is disposable** — leases, presence and pub/sub only. Losing it delays
  or repeats a notification; it never loses a business row. A lease is not a
  commit barrier; the advisory locks and row revisions are.
- **Job lease/fencing** — a worker owns a job only while its 30-second lease is
  valid; a stale attempt cannot publish `done` or claim an artifact. Exports
  publish the file only after a clean `.part` → `.xlsx` rename.
- **Import atomicity** — the worker commits plan writes, recompute and the
  terminal job result in one transaction; a cancelled job or a lost lease rolls
  all of it back. Blank source cells overwrite (NULL), only a column absent
  from the file keeps its value, and duplicate plan codes all get overwritten
  (the uniqueness scope is still a Gate B/C cleanup task). The undo stack is
  cleared in the same transaction.
- **Export artifact visibility** — a download becomes visible only after
  successful completion; a partial or stale attempt never replaces it.
- **Authorization is enforced server-side** on every request: role/block/asset
  checks in the service layer, not only grid read-only flags.
- **Conflict policy (Gate A)** — while an import runs, the plan form is blocked
  and writes are refused with 409; stale cell leases expire on their TTL.
- **Filtering happens before pagination** — `WHERE` is applied before the
  window/count; default reads use a keyset seek, sorted reads use OFFSET.
- **Applied migrations are immutable** — corrections are new revisions only.

## Transactional collaboration

PostgreSQL stores business data, `collab_edit_stacks`, `collab_cell_states` and `collab_outbox` in one transaction. The outbox retries Valkey publication after commit. SQL sessions are request-scoped, not WebSocket-scoped; HTTP mutation dependencies commit before response delivery. See `COLLAB.md` for locks and recovery behavior.

Renovation approval copies the plan with PostgreSQL INSERT SELECT rather than constructing all rows in Python. Submission deletes empty draft rows with SQL predicates. Cell edits batch-load affected rows and serialize updates of the same row to preserve derived calculations. No load-test latency or capacity certification is implied; the planning target is 50 concurrent users across projects.

The renovation plan's 59 formula fields are **stored columns**, recomputed on every edit and read back verbatim by `GET /plan/rows` — recomputing them per read is infeasible (~90 s for the 306k-row plan) and SQL filters/facets need them as columns. The importer skips calculated fields, so imported/adopted rows are filled by `recompute_plan_rows` (`just recompute-renovation-plan`, also run at the end of `just seed`/`just seed-renovation` for their demo rows); see `docs/WORKFLOW.md`. The importer passes the ids of the rows it just wrote, and the recompute writes only rows whose derived values changed: rewriting all 306k rows/133 calculated columns (≈0.95 ms each, ~290 s) was the dominant cost of a ~20k-row load. The full sweep remains the backfill; the import job additionally exposes `parse`/`write`/`recompute` phases and a progress total of twice the preflight row count.

**The renovation ID (`work_or_repair_code`) is NOT one of them** — the generator is currently off (`RENOVATION_ID_FORMULA_ENABLED = False` in `back/src/renovation/renovation_id.py`): the field is a plain editable text cell, loaded from the template as-is and typed by hand, because the agreement processes and the state of the data made a derived value impractical. `form_schema.CALCULATED_OVERRIDES` / `READ_ONLY_OVERRIDES` drop its legacy «calculated + read-only» flags without touching the legacy JSON entry, so the whole generator (`calculate_renovation_id`, its component validation, the import's correctness check) stays in the codebase and can be switched back on from that one flag. While it is off the import keeps **only the duplicate check** as a blocker («Некорректно рассчитанные коды» is archived in the UI), the «Некорректные» filter means *empty or duplicated*, and submit refuses an empty or repeated code. The «Лот» (`lotting_activity_direction_code`) code, by contrast, always ends with a dot: a correct code typed or imported without it gets it added silently (`renovation_id.normalize_direction_code`).
