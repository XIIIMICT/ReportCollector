# ReportCollector Architecture

Raw-material planning system: Excel-like forms with real-time collaborative editing. Monorepo: `front/` (Next.js), `back/` (FastAPI), `infra/` (compose of external services), `docs/`.

Sections: [System diagram](#system-diagram) · [Services and compose profiles](#services-and-compose-profiles) · [Environment variables](#environment-variables-root-env-single-source) · [Data model «Сырьё»](#data-model-project-сырьё-key-raw_materials) · [Dictionary ordering](#dictionary-ordering) · [Data model «КППК»](#data-model-project-кппк-key-kppk) · [Authentication](#authentication) · [API](#api-back-prefix-api) · [Logging](#logging) · [Deployment](#deployment-prod) · [Plan import](#renovation-plan-import-technical-directorate-xlsb--xlsx) · [Export jobs](#renovation-plan-export-jobs) · [Transactional collaboration](#transactional-collaboration)

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
- `AUTH_SESSION_MAX_AGE_SECONDS` — session lifetime, default 43200 (12h), **independent of the short Keycloak ID-token TTL** (otherwise "not authorized").
- Session cookies require a valid HMAC, exactly two token segments, nonempty subject/issuer and a finite unexpired numeric timestamp; malformed signed payloads are rejected too.
- `AUTH_SEED_ADMIN_EMAIL` + `AUTH_SEED_ADMIN_PASSWORD` — seed admin (password = hash for local login; the email activates on first Keycloak login).
- Corporate-proxy compatibility is retained: backend dependency downloads allow insecure `pypi.org`/`files.pythonhosted.org`; frontend Bun build/dev stages use `NODE_TLS_REJECT_UNAUTHORIZED=0`. This is an explicit certificate-verification exception, not a security best practice. It is not inherited by the Node production runtime. Prefer a trusted corporate CA when available; do not remove the existing workaround without testing downloads behind the proxy.
- Database migrations run from the release checkout on the host/CI with uv, not inside runtime images. See `docs/WORKFLOW.md` for the one-time guarded adoption of `0001_baseline` by existing installations.

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

## Data model (project «КППК», key `kppk`)

Phase 1 copies the source tables of the corporate КППК database into the program
schema and exposes the four dictionaries the «Таблицы.xlsx» phase builds on.

The **original data is not in the repository**: `raw.kppk_*` ships as SQL dumps
the operator loads into the database. The seeder only needs the database — plus
three small files of its own in `back/src/kppk/data/`:

- `source_schema.sql` — the structure of the source tables (a fresh or CI
  install creates them from it);
- `demo_source_rows.sql` — a few rows (13 ДЗО, 137 products, one period, one
  row per form) used **only** when the source tables are absent, so every form
  works end to end on a demo install. A real installation loads the actual
  dumps instead and this file is never merged with them;
- `product_matrix.sql` — the product × ДЗО matrix **and** its «Заполняемый
  акциз» flags. This is the one input the source dumps never carried (they have
  products and ДЗО as separate dictionaries); it replaced «Матрица
  продуктов.xlsx» and is the reason no spreadsheet is needed at seed time.

The `dict` schema belongs to another project and is never created or touched.

- **Source schemas** `raw` (`raw.kppk_d_org`, `raw.kppk_d_period`,
  `raw.kppk_d_product_seq`, `raw.kppk_d_raw_seq`, `raw.kppk_f_realization`,
  `raw.kppk_f_realization_plan`, `raw.kppk_f_production_plan`) — created from
  `source_schema.sql` and filled with the demo rows **only when missing**; on
  prod installations they already hold the real data and are only read. The
  `dict` schema is another project's and is never touched.
- **Program copies** (`back/src/kppk/models.py`, prefix `kppk_`): `kppk_orgs`,
  `kppk_periods`, `kppk_products`, `kppk_raws`, `kppk_markets` (the
  dictionaries), `kppk_org_products` («Продуктовые позиции»: product × ДЗО links
  from the matrix), and the read-only `kppk_realization` /
  `kppk_realization_plan` copies for the next phase. Every copy is uuid-keyed;
  the source integer id stays as a unique column (`org_id`, `period_id`,
  `product_num`, `raw_num`) — the seeder's re-copy key and the join key the
  fact/plan copies reference. `product_num`/`raw_num` are admin-editable in the
  UI and stay unique; the dictionary names are unique as well
  (`kppk_orgs.code`/`name`, `kppk_products.name`, `kppk_raws.name`,
  `kppk_markets.code`/`name`, `kppk_periods` by `(month_name, year_num)`);
  several NULL source numbers stay allowed (PostgreSQL treats NULLs as distinct)
  — the matrix brings in products without a number.
- **Normalized references (0024).** The source dumps carry the product NAME and
  the market CODE as free text in every realization row. The copies reference
  them by uuid instead: `product_id` → `kppk_products.id` and `market_id` →
  `kppk_markets.id` (`ON DELETE RESTRICT`) on `kppk_realization`,
  `kppk_realization_plan`, `kppk_cost` and `kppk_production_plan`; the text
  columns are gone. The one and only name comparison happens in the migration
  back-fill (and in the seeder's copy step), and both **abort on an unmatched
  row** rather than skipping it. `kppk_markets` holds «РФ»/«ДЗ»/«БЗ» (code +
  full name), seeded by the migration; the seeder tops it up with
  `ON CONFLICT DO NOTHING`. `uq_kppk_cost_scope` keys on
  `(org_id, period_date, product_id)`. The API contract is unchanged
  (`product`/`region` stay strings) — the backend joins the dictionaries.
- **Seeder** `just seed-kppk` (`back/src/scripts/seed_kppk.py`, one
  transaction): re-copies the source tables on every run (truncate + re-insert,
  audit stamps on the four dictionaries) and rebuilds `kppk_org_products` from
  the matrix; products and ДЗО are matched by exact display name — no
  normalization (spaces stay significant). The realization/production copies
  resolve that text into `product_id`/`market_id` on the way in, and the seed
  FAILS on a source row whose product or market is not in the dictionaries
  instead of writing an unresolvable copy. The ДЗО copy keeps the source id,
  code and name only — `raw.kppk_d_org.time_zone` is not copied. A matrix
  product missing in the source dictionary is created without a `product_num`
  under an `@`-prefixed name; a dictionary product missing in the matrix keeps
  an empty ДЗО. The market dictionary is topped up to the source codes («РФ»,
  «ДЗ», «БЗ») when a restored schema left it empty (`markets_created` in the
  counters). The `kppk` project row and its user links are owned by
  `just seed` / self-provisioning, not by this seeder.
- **Workspace** `front/src/features/kppk/kppk-workspace.tsx`: three static tabs
  over the copies («Продуктовые позиции» follow the «Сырьевые позиции» form —
  the source number «№» leads (editable, unique server-side), then the product
  with its ДЗО multi-select column, values comma-separated; «ДЗО» keeps the
  code and the full name only — ordered by the name, no number, no time
  zone). The «Периоды» dictionary is backend-only in this phase — its tab is
  hidden until «Таблицы.xlsx». Tabs are visible to every project member,
  editing is admin-only (mirrored by a read-only grid). No realtime
  collaboration (no WS room) and no fixing in this phase, but the shared
  action-bar mechanics are in place: undo/redo through the server-side
  collaboration journal (page `session_id`, history per tab) and Excel export
  of the whole/visible table through the common dictionary renderer.

## Authentication

Two modes by `KEYCLOAK_ENABLED`:

1. **Keycloak (OIDC):** browser → `/api/auth/login` → authorization code flow (PKCE S256, scopes `openid profile email`) → callback `/api/auth/callback` → `rc_session` cookie (own lifetime `AUTH_SESSION_MAX_AGE_SECONDS`). First login fills/activates the user from Keycloak data (FIO overwritten, email lowercased, lookup case-insensitive). Flow, prod config, claims and provisioning — `docs/KEYCLOAK.md`.
2. **Local login** (`KEYCLOAK_ENABLED=false`): `POST /api/auth/login` (email + password, Argon2), e2e tests use it. Rate limit 20 req/min per IP on `/api/auth/*`.

Session — `rc_session` cookie (JWT-ish, jose; Secure when `https://` is in `AUTH_APP_ORIGIN`). Proxy middleware `front/src/proxy.ts` (matcher `/`, `/blocked` and the project URLs) redirects without the cookie to `/api/auth/login`.

Project URLs — `/raw-materials`, `/renovation`, `/kppk` (root redirects to the first accessible project, preferring renovation). Membership is never created by the root: `GET /api/me` runs with create=False. Opening a project URL calls `POST /api/me/projects/{key}/request-access`, which creates the user record (when missing) and a single inactive project link — the project admin grants access from «Пользователи». Keycloak/callback always return to the app root, so the originally requested path travels in the `rc_auth_return_to` cookie.

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
  - Edits: `PATCH /movement`, `PATCH /prices` (cell batches → `cell:changed` into the room), dictionary CRUD (admin).
  - Undo/redo: `GET /undo-state?section=&report_month=`, `POST /undo`, `POST /redo` (scope in the body).
  - Fixing: `POST /movement/fix` (admin) — see "Landmines" in AGENTS.md.
  - `POST /export` — Excel (openpyxl): whole/visible selection, for «Движение» — archive versions.
  - Renovation plan export — **background jobs**: `POST /plan/export/jobs` (202 + job id), `GET /plan/export/jobs/{id}` (status/progress), `GET /plan/export/jobs/{id}/file` (download). The legacy `GET/POST /plan/export` endpoints remain for compatibility: bounded requests render inline, while larger scopes are admitted to the same durable job boundary.
  - Renovation plan **import preflight** (admin): `POST /plan/import/inspect` (multipart `file`, `.xlsb` or `.xlsx`) validates the technical-directorate workbook **without writing** to the database. Readiness and the response contract — see «Renovation plan import» below.
  - Renovation plan **import load** (admin): `POST /plan/import/apply` (multipart `file`, `.xlsb` or `.xlsx`) stages the checked workbook and returns `202` with a durable job. Poll `GET /plan/import/jobs/{id}` (or cancel with `DELETE`) while a worker parses and writes the workbook under the project gate. See «Loading the checked file» below.
- `kppk` (signed identity; reads for every project member, mutations admin-only): dictionary CRUD — `GET/POST/PATCH/DELETE /product-positions[/{id}]` (PATCH `{field, value}`, `field` = `name` | `orgNames` | `productNum`; create accepts `productNum`; delete removes the product with its links), `/raws` (PATCH `{field, value}` for `name`/`rawNum`), `/orgs` (PATCH `{field, value}` for `code`/`name`), `/periods` (PATCH `{field, value}`; no UI in this phase). The source number («№», `productNum`/`rawNum`) is editable and unique — an occupied number is rejected with a clear `400`; `null` stands for a row added by hand (several NULLs are allowed); positions/raws are returned in the source-number order (unnumbered rows last). The «ДЗО» list is ordered by the full name and exposes no number. Undo/redo join the shared collaboration journal: every mutation carries the page-lifetime `session_id` (query) and is journaled as one action scoped to the tab (`scope = f"{section}/"`, no report month; `collab/undo.py` dispatches `kppk_products`/`kppk_raws`/`kppk_orgs` to `apply_kppk_cell_value`) — `GET /undo-state?section=&session_id=`, `POST /undo`, `POST /redo` (body `{skipActionId, section}`, admin; no WebSocket room and no `cell:changed` — the front refreshes the grid itself after undo/redo). `POST /export` streams the tab as XLSX (whole/visible via `rowIds`, `columns` from the grid). Deletes follow RESTRICT: a ДЗО/period referenced by the fact/plan copies is rejected with a clear `400`.
- `ws`: `WS /ws/raw-materials` — collaboration (see `docs/COLLAB.md`).
- Service: `GET /health` (process liveness), `GET /ready` (PostgreSQL/Valkey readiness, including the required outbox migration), rate-limit and log middlewares in `back/src/main.py`. HTTP responses carry `X-Request-ID`, also recorded in structured request logs. Granian access/handshake logs redact credential query parameters (`ticket`, `access_token`, `token`); the external reverse proxy must apply its own redaction policy.

Errors: `CellValidationError` → 422, `DictionaryReferenceError` → 409, `ValueError` (not found) → 404, no access → 403. Clear Russian `detail` messages — the front shows them via `readApiDetail` (`front/src/shared/api/raw-materials.ts`).

## Logging

`back/src/app_logging.py`: structured events (`application_started`, `http_request`, `collab.ws_*`, `keycloak_identity_received`, …) to the `LOG_FILE` file and to Loki via Alloy; viewing — Grafana (`{app="reportcollector"}`). No tokens/secrets in logs. The Alloy config stamps every line with `app=reportcollector`, `job=reportcollector` and `env=<LOG_ENV>` from `compose`/`.env` (`local` by default; set `prod` on the server).

The file rotates (`RotatingFileHandler`, 64 MB × 5 backups — at most ~320 MB; a plain handler reached 174 MB in 33 hours). Rotation **renames** the active file, so Alloy's path-based tail re-reads the new one on its 5 s `sync_period`; a handful of lines can be missed at a rollover, which is the accepted price of a bounded disk. Loki keeps 7 days (`retention_period: 168h` + the compactor that actually enforces it) — without both, retention is inert and chunks are never deleted.

`request_id` is recorded by the middleware, returned to the client in `X-Request-ID`, and ingested as Loki **structured metadata** (not a label — a uuid per request would explode the index). Trace one request end to end with `{app="reportcollector"} | request_id="<id>"`.

## Monitoring

Logs answer "what exactly happened"; metrics answer "how many and how often". **Percentiles exist only in metrics** — they are computed from histogram buckets at write time and cannot be derived from log lines.

- `back/src/metrics.py` — a dedicated `CollectorRegistry`; `/metrics` (unauthenticated, aggregate values only) is scraped by Prometheus.
- HTTP: `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` (buckets 5 ms…30 s), `http_requests_in_progress`. The `route` label is the **route template** (`/kppk/product-positions/{position_id}`), never the raw path — otherwise every row id would create its own time series; unmatched paths share `route="unmatched"`. `/health` and `/ready` are skipped in the LOG but still measured.
- Collaboration: `ws_connections_active`, `ws_rooms_active`, `ws_events_relayed_total{room_section}`, `ws_slow_consumers_total{room_section}`. The last one matters most at scale: a client whose queue fills is **disconnected on purpose** and reconnects silently, so nothing errors — only this counter reveals it. `room_section` drops the month and the ДЗО scope, keeping one series per form.
- Authentication: `auth_rate_limit_events_total{storage,outcome}` records shared Valkey, local fallback, allowed and rejected login-window decisions. Argon2 password work uses a dedicated bounded executor so expensive hashing does not occupy the ASGI event loop.
- Dashboards (`infra/grafana/provisioning/dashboards-json/`): **Сервис** (HTTP percentiles, database pool/query/lock pressure, import/export phases and queue, Valkey/dependency health, RSS/event loop/disk), **Сотрудничество** (WS load, slow consumers), **Ошибки** (Loki, with `request_id` correlation). Provisioned from git, `allowUiUpdates: false` — edit the JSON, not the browser.
- Prometheus scrapes `host.docker.internal:<BACKEND_PORT>` (15 s, 15 days retention); the port is in `infra/prometheus/prometheus.yml` because Prometheus does not expand compose variables.
- **Alerts** (`infra/prometheus/alert-rules.yml`, 12 rules): a 5xx burst (>0.05/s for 2 min), an immediate recent server/interrupted-response failure, accumulating 5xx (>20/h), `up == 0`, collaboration slow consumers, pool exhaustion, query timeout/deadlock failures, export failures, event-loop lag, disk pressure, readiness dependency failures and Valkey subscription failures. The immediate rule ignores the initial zero gauge and expires five minutes after the last failure. The event-loop/disk/capacity values are operational guardrails; load-based latency and memory thresholds remain to be calibrated. Alertmanager groups them (`group_wait 30s`, repeat 4h) and exposes them at `http://localhost:9093`.
- **Mail delivery was verified 2026-10-27**, when a test alert produced `msg="Notify success"` and arrived at `SUPPORT_MAIL`. The current dev Alertmanager log (2026-10-29) shows repeated `connection refused` to `smtp.mail.ru:587`, so delivery is currently an operational item to recheck rather than a passing claim. `entrypoint.sh` renders `alertmanager.tpl.yml` with `ALERT_SMTP_*`/`SUPPORT_MAIL` from `.env` (editing `.env` needs `just monitoring-stand`). The smarthost must be **587** (465 is implicit TLS and `smtp_require_tls` expects STARTTLS), and `ALERT_SMTP_USER` must be the sender mailbox, the same one as `ALERT_SMTP_FROM`. Remaining alert and load work is tracked in `docs/TODO.md`.
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

### Bundle shape (why the first load is heavy)

`ReportWorkspace` used to import all three project workspaces statically, so opening «Движение» also downloaded the КППК and renovation grids — and a grid is `@revolist/react-datagrid`, the bulk of the largest chunk. They are now `next/dynamic`, one per URL: the largest chunk went **2 179 KB → 1 459 KB**, and the grid split into its own 746 KB chunk that only the project that needs it loads. Adding a new project to `report-workspace.tsx` means adding another `dynamic(...)` entry, not a static import.

## Renovation plan import (technical directorate, `.xlsb` / `.xlsx`)

`back/src/renovation/import_plan.py` validates an uploaded «РБ ПОФ» workbook
before anything is written. Columns are matched **only by header names** —
normalised (case, whitespace, quotes), never by positions, since columns are
added/removed between drops. The header may be row 1 or row 2 (row 1 then holds
the extra info); a numbering helper row under the headings is detected by shape
and skipped. `TEMPLATE_COLUMNS` freezes the headings of the current sample; a
missing column is simply not loaded, an unknown one is ignored. Only two
headings need an alias: «Лот» → direction code (`col_189`) and the
suffix-free «Отнесение затрат (OPEX/CAPEX)» / «Способ выполнения работ».
Asset identification uses «Наименование ДО» — the official repair code
(`BSZR`) that is also an input of the work ID; the UI shows the asset name.
Text cells are stored trimmed (a non-breaking space included) and an empty
string counts as an empty cell; the «Лот» code gets its mandatory trailing dot
added silently when the file has `1.1.1` instead of `1.1.1.`
(`renovation_id.normalize_direction_code` — the same rule the grid applies on
edit).

A date is read from its **value**, not from the workbook's declared cell type:
the customer's files routinely store a correct date as text (the column or just
the cell is formatted «Текстовый»), so `05.11.2003`, `5.11.03`, `05/11/2003`,
an ISO string and any of those with an optional time are all accepted and
normalised to a `date`. Only a value that is not a real calendar date
(`31.02.2003`, arbitrary text) stays a «Неверный тип данных» error. The apply
pass shares the parser, so the preflight and the load agree on what a date is.

Both containers the customer drops are accepted, chosen by the upload's suffix:
`.xlsb` is read with `pyxlsb`, `.xlsx` with `openpyxl` (`read_only`,
`data_only`). The OOXML reader therefore sees the formula **cached values** the
last Excel save left behind — it never re-evaluates a formula; the plan's own
recompute rebuilds the derived columns after the load regardless.

Checks, in order: the file must be a readable `.xlsb` or `.xlsx` whose header
carries
«Код работы» plus the asset code and at least half of the template columns;
cell types under the header (text/number/date); duplicate work codes
(«Код работы», listed with the rows they occupy) — the **only** blocker on the
code itself, since the code is loaded from the file as-is (the ID formula is
off, see «Renovation plan» below; the «Некорректно рассчитанные коды» check is
archived in `inspect_plan_file` behind `RENOVATION_ID_FORMULA_ENABLED`); an
asset whose code is not in the project's `rm_assets` dictionary
(`unknown_assets` — the asset is a reference value like any catalog label);
and reference labels absent from the form's own catalogs, compared strictly
(whitespace/case only — no typo tolerance, no «@» options). Without errors the
response carries the assets (by name), the new rows and the rows to update
(existing `col_003`).

The preflight dialog (`plan-excel-import-modal.tsx`) closes only by its close
button, Escape, or the «Загрузить» hand-off: the backdrop is
`isDismissable={false}`, so a stray click outside cannot discard a checked file
or interrupt an upload.

The upload body is bounded by `ImportUploadLimitsMiddleware` before multipart
parsing, then Starlette's `UploadFile` keeps it in its spooled temporary file.
The inspect endpoint hashes and parses it through bounded row iterators; its
scope-discovery pass (asset codes plus the reference catalogs the template
header uses) remains intentionally separate until a future one-pass preflight.
Apply stages the bytes and does not parse them in the HTTP request.

`RENOVATION_IMPORT_MAX_BYTES` defaults to 256 MiB for each file. The HTTP body
limit adds a 1 MiB allowance for multipart boundaries/fields and also covers
chunked uploads without `Content-Length`. `RENOVATION_UPLOAD_TIMEOUT_SECONDS`
(300 seconds) bounds receiving the body, excluding subsequent parse/apply time.
Oversized or slow bodies return 413/408, and the multipart parser closes partial
temporary files. Match proxy upload limits to these values in production.

Reports are cached in Valkey (`src/renovation/inspect_cache.py`): the key is
`rc:planimport:<sha256 of the bytes>:<scope>`, where the scope has two parts.
The plan part is the file's assets («Наименование ДО» repaired codes → asset
ids) plus the newest `updated_at` of the working plan rows **of those assets**;
a plan edit in any other asset therefore keeps the cached report valid, while
touching a loaded asset recomputes it. The dictionary part carries
`count(*) + max(updated_at)` of every reference catalog the file has a column
for (`scan_file_scope` maps the template header through the form schema, so a
catalog the file never mentions cannot affect its report) and the same token
for the asset entries the file names. Adding the value a cached report called
missing therefore changes the key and lets the same file be re-uploaded
immediately instead of serving the stale error until the TTL. The TTL is one
hour. The cache is optional — without `VALKEY_URL` or when Valkey is
unreachable the file is analysed again.

`inspect_plan_file` returns only; the actual import is a separate step.

### Loading the checked file

`POST /plan/import/apply` (admin, multipart `file`, `.xlsb` or `.xlsx`) stages the
validated workbook and returns `202` with a durable `renovation_import` job.
The accepted bytes live under `IMPORT_DIR`; the job payload stores only a
digest, plan id and internal input key. `GET /plan/import/jobs/{id}` polls the
bounded result and `DELETE` cancels it. A supervisor claims the PostgreSQL
lease and starts `src.scripts.import_worker` in a separate Python process, so
XLSB/XLSX parsing and plan recomputation do not share the ASGI interpreter or its
log handler. The child takes the project gate, streams batches of 200 and
commits the plan writes **and** the terminal job result in one transaction.
Cancellation/lease loss therefore rolls the transaction back; a lost HTTP
response cannot cause a second apply. The input is removed only after a
terminal job state or TTL cleanup. The gate still covers the full write and
recompute phase; staged/versioned finalization is the next scale slice.

The job reports progress per phase: `parse`, `write`, `recompute`. The client
sends `expected_rows` (the preflight's `new_rows + rows_to_update`), so the
worker's `total` is twice that — one half for the write, one for the recompute
of exactly those rows — and the terminal result overwrites it with the actual
row count. The manager persists a progress change within a second (it polls the
child's in-memory counters, while the 30-second lease still renews every 10 s),
which is what feeds the footer's determinate progress bar: pressing «Загрузить»
closes the dialog, the load continues behind a bar that names the current stage
and states the percentage, centered on the footer itself — the status and the
action bar take equal tracks around it, so it holds the panel's center instead
of the gap their content leaves. Just above `lg` the full action bar needs more
than its half, and the bar gives that width back (it never covers the buttons);
below `sm` it halves to 10em and the status summary collapses to its badge,
while the status lines ellipsize rather than run under the bar on a tight
footer. Only the terminal outcome is a toast. The bar survives a switch
between the project's forms (the same workspace instance) and re-attaches after
leaving and returning to the project: the job id is kept per project in
`sessionStorage` (`front/src/features/renovation/use-plan-import-job.ts`), so a
remount resumes polling until the job settles. The analysis wait before it
keeps its indeterminate bar inside the dialog.

Rows are matched by «Код работы» (`col_003`): a code already in the plan updates
that row in place, a code the plan does not have **appends** a row at the end
(picking up the positional sequence after the current last row). A cell the file
leaves empty keeps the plan's current value — a blank cell is never an
instruction to clear a value. The write boundary is thin but not free: the parser
hands dates over as ISO text (the same value feeds the renovation-ID rule) while
`col_024/025/191/199/203` are real `DATE` columns, so `_flush_batch` converts
them; and because the parser **drops blank cells**, the rows of one batch carry
different key sets — a multi-row `INSERT` demands identical keys per row
(`CompileError: … explicitly rendered as a boundparameter`), so every row is
padded to the batch's key union and the cell it omitted becomes `NULL` on a
created row (on an existing row it still means «keep the current value»).

Reference cells are resolved to the ids of the **live** `r_reference_values`
dictionary — the same rows the preflight (and the «Справочники» editor) sees.
Both passes therefore agree: an option the operator adds after the seed is
loadable at once. Reading the seed's static `REFERENCE_OPTIONS` here instead was
a trap — the preflight said «ready» and the load then refused the whole file
(«значение … не найдено в справочнике»). That is the «Собственными силами» case:
the customer's file spells it in the instrumental case while the seed carries
«Собственные силы»; likewise the file's «ОКР» and «ДО» are abbreviations the seed
spells out («Остановочный капитальный ремонт», «Диагностические обследования»),
so the operator adds them as options of «Категория работ» / «Вид работ». Numeric
cells lose their float tail first
(`3.0` → the label `3`). A label no dictionary row carries is a **strict
error**: the preflight lists it under «Значение отсутствует в справочнике» and
the load fails the job with a Russian error and rolls back the whole
transaction.
The same strictness covers blank required cells — «Код работы», «Предприятие»,
«Группа критичности», «Категория работ», «Вид работ», «Отнесение затрат»,
«Способ выполнения» — reported in the `blank_values` section; a row without a
work code is not loaded at all. The **asset** is held to the same rule: a code
missing from the project's `rm_assets` (`unknown_assets` in the report) refuses
the whole file instead of silently skipping its rows — nothing is dropped from
the plan, so the response no longer carries a `skipped_rows` counter.

The load is **not** journaled into undo/redo: it rewrites many rows at once, so
a single «Отменить» cannot meaningfully revert it.

**Known risk (temporary, until the load leaves the testing phase):** the gate
serialises the load against other *gated* writes (cell edits, row inserts,
fixing), but a user who is holding a lease on a cell of a row the file rewrites
keeps their stale value in the open editor and can commit it afterwards — the
last write wins. Undo history of the affected rows is not reconciled either. No
special handling yet; the operator should avoid loading while someone edits the
same rows.

## Renovation plan export jobs
Excel-export plumbing is shared by every project in `back/src/common/`:
`xlsx_stream.py` carries the raw-XML streaming mechanics (static package parts,
the sheet prefix with the frozen pane, cell builders, `merge_cells_xml` with
first-writer-wins range dedup), `xlsx_table.py` renders the standard table
workbook (multi-level `headerPath` headers with merges, live deviation
formulas, datetimes in the shared format, «Да»/«Нет» booleans, dropdown lists
from a hidden «Списки» sheet — the exact semantics the openpyxl renderer had),
and `export.py` carries the request validation (limits, uniqueness, header
shape, file name, per-section allowlists). A project module keeps only its
vocabulary — sheet names, allowed keys, which column references which list —
and calls into here (`src/raw_materials/export.py`, `src/kppk/export.py`,
`src/renovation/export.py`). The renovation PLAN keeps its specialized
sheet-layout writer (the 11-level Excel header, the aggregating band, 59 live
formula columns) built on the same `xlsx_stream`.

Every table export is **dual-mode behind one endpoint** (`export_table_or_job`
in `src/common/export_jobs.py`): up to `INLINE_EXPORT_MAX_ROWS` (5 000) rows
the workbook renders right in the request and returns as an attachment; past
the threshold the response is `202` + a job description and the shared
background worker builds the same workbook — the front hands the job's file
URL to the browser, which downloads the file WHILE it is still being
generated (the append-only container makes the served bytes valid from the
first byte). The renovation plan, КППК cost/realization, КППК and renovation
dictionaries, and «Сырьё» table exports use a durable PostgreSQL query
specification (`data_jobs`) and bounded cursor batches in the isolated worker;
the worker reopens the authorized query instead of receiving materialized rows.
Small exports remain inline under the configured row limit.

A full plan export is ~306k rows × 216 columns: minutes of CPU and ~500 MB of
XLSX (the sheet XML exceeds 4 GB uncompressed → ZIP64). Doing that in the
request handler froze the whole event loop for every user and several parallel
requests OOM'ed the container. The export is therefore a background job
(`back/src/renovation/export_jobs.py`):

- `POST /plan/export/jobs` resolves the scope (plan + asset + block + header
  filters + optional rowIds), computes the export stamp (last row edit for the
  working plan) and enqueues a job; the response is immediate. An identical
  scope+stamp+renderer version reuses the ready file. The cache key includes
  `PLAN_EXPORT_RENDER_VERSION`; bump it after rendering/materialization fixes
  so an unchanged query cannot reuse an outdated workbook.
- A PostgreSQL claim bounds execution to `EXPORT_WORKER_CONCURRENCY` active plan exports across backend
  processes. The consumer supervises a child Python interpreter that streams a
  repeatable-read snapshot through the XLSX writer. Its CPU work has a separate
  GIL from HTTP/WebSocket handling. `EXPORT_DIR` defaults to an `exports/`
  subdirectory beside `LOG_FILE`; the existing compose log volume persists it.
  Concurrency defaults to 1 (allowed 1–8); set the same value on every replica
  and raise it only after measuring RAM/CPU/DB headroom. Extra slots use separate
  Python processes and can therefore execute CPU work in parallel.
- **Throughput.** The row read selects the `col_*` value columns directly; the
  earlier `jsonb_strip_nulls(to_jsonb(row) - keys)` sparse-values build measured
  ~2 ms/row on the 306k-row plan (~10 of the 13 minutes), the direct read
  ~0.1 ms/row. The formula engine now runs only for rows that miss a calculated
  value (recalc happens on edit; adopted rows may never have run it), and the
  ~130 per-row Excel formula templates are XML-escaped once at layout build time
  instead of per cell — the full export went from ~13 min to ~1.7 min. The
  workbook sets `fullCalcOnLoad`, so Excel recalculates on open.
- **Source values.** The formula engine compiles from normalized `field_metas`,
  including application overrides, never directly from legacy JSON. Recomputing
  missing derived values must preserve editable `col_003` work codes; its old
  calculated flag used to erase codes in otherwise valid exported rows.
- Limits: 8 durable active jobs globally, 2 per user → `429`. Admission and
  deduplication are transactional. Same-user scope+stamp requests share one job;
  another user receives a separate owner record/file. Terminal jobs expire one
  hour after completion. The same durable admission boundary applies to every
  query-owned table export; only small inline responses bypass the queue.
- A 30-second lease is renewed every 10 seconds. A lost worker retries with
  backoff, up to three attempts; validation/rendering failures do not retry.
  `DELETE /plan/export/jobs/{id}` cancels an owner's pending/running job. This
  retains the frontend-compatible `failed` status with a cancellation message.
- The workspace starts the job and then hands the file URL to the browser as a
  **native download** (an `<a download>` click on `/plan/export/jobs/{id}/file`).
  The endpoint **streams the growing file** (`DurableExportManager.stream_growing`): it
  flushes an empty first chunk so the browser's download appears at once, then tails
  the `.part` file the worker writes and forwards bytes as they land — not after
  generation finishes, which for a 300k-row plan is minutes of silence. The browser
  therefore shows its own download UI (a
  `.crdownload` that becomes the finished `.xlsx`) with the progress bar moving
  the whole time, and because the browser owns the download, closing the tab or
  reloading does not cancel the generation (it runs in the background worker) or
  the download itself. The response is `Transfer-Encoding: chunked` (the final size
  is unknown until generation ends), so the browser shows downloaded bytes rather
  than a fixed percentage — measured on the full 306k-row plan: first byte in
  ~0.3 s, 523 MB streamed over ~92 s (a 26k-row filtered export: ~0.5 s / ~10 s).
  Nothing is buffered in page memory.

`data_jobs` persists the query specification, owner, progress, attempts and
lease token. Each attempt writes `{job_id}-{token}.part` and atomically renames
it to `.xlsx`; only the current, unexpired token can publish `done` in the DB.
A download pins its first attempt and fails if that attempt is interrupted:
it must never concatenate bytes from a retried ZIP. Start a new download after
the retry completes. Reads use 64-KiB chunks, and missing completed files become
an explicit failure so a new request can regenerate them.

All backend replicas must mount the **same** `EXPORT_DIR`. The default existing
volume covers local restarts and same-host deployments, not independent hosts.
Shared object storage/dedicated worker deployment remain Phase B follow-ups.
The historical throughput measurements above predate process isolation and
must be repeated during Phase D. Child-process DB/RSS/CPU metrics currently do
not join the API registry; job metrics and logs are emitted by the supervisor.

### Exported workbook layout

The exported `.xlsx` mirrors the grid exactly: 11 merged header rows, then the
**aggregating band of two rows** — the **number row** (12) carrying each exported
column's workbook ordinal − 1 (so «Предприятие» = 1) and the **totals row** (13)
carrying «Итог» plus a live `SUM` over the exported data rows for every
additive numeric column (the exact range `first..first+row_total−1`; with no
promised rows the totals carry the cached value only, without `<f>`). A column
with no total **merges its number over both rows** (`B12:B13`), exactly like the
grid's number-over-empty cell; a column with a total keeps the two rows separate.
Data rows start dense at row 14: a filtered
export keeps each surviving row's stored plan position in column A, but the
workbook row is a compact output index, so a filter never leaves thousands of
blank rows after the header. The pane freezes the top 13 rows (header + the two
aggregating rows) like the grid.

Excel repair-dialogs («файл повреждён») on duplicate `<mergeCell ref>` or on
`<row>` elements out of ascending `r` order. The row layout is therefore
**canonical**: static parts → header rows → number row → totals row → data rows,
all written into `RenovationWorkbookWriter.__init__` before the first data
batch. The writer also carries a `row_total` **promise** of the data-row count
given by the caller (the job passes `read_plan_export_meta`'s `total`, the
streaming endpoint the materialized `len(plan.rows)`): more rows than promised
fail the write at once, fewer fail at `close()` — both raise
`ExportRowCountMismatchError` («…количество строк плана изменилось во время
выгрузки…»), and the worker deletes its `.part` instead of renaming a broken
file — the totals' live `SUM` needs the exact range, so the count is checked,
not assumed.

The container matters just as much as the XML. `zipfile` on a seekable sink
writes each entry a **draft local header** (zero CRC and sizes) and patches it
by seeking back at `close()`. The download streamer tails the growing `.part`
(see below), so the browser receives the draft bytes and never the patch — the
downloaded archive is born invalid and Excel repair-dialogs it with a bare
«проверка на уровне файла» (a full 520 MB production export hit exactly this).
The writer therefore wraps its sink in `xlsx_stream.UnseekableSink`: the
non-seekable proxy switches `zipfile` to **data-descriptor entries** (bit 3 —
"sizes follow the data"), so every byte is written exactly once, in order, and
the served stream equals the finalized file. It deliberately stays on plain
ZIP, not zip64: a streamed zip64 entry's local header carries an all-zero
zip64 extra field, which real Excel rejects outright (COM-verified: a
descriptor entry without zip64 opens clean, with zip64 fails; the re-zipped
3.5 GB production sheet — 306k rows — opens in ~3 min). CPython 3.14 lowered
`zipfile.ZIP64_LIMIT` from 4 GiB to 2 GiB out of caution, which would push the
~3.3 GiB real sheet into zip64; `xlsx_stream` restores the format's true
4 GiB − 1 ceiling on import. Past that ceiling the write cannot be streamed
safely at all, and `close()` raises `ExportTooLargeError` («Выгрузка
превышает 4 ГБ — Excel не сможет открыть такой файл…») — the job fails with a
user-facing message instead of producing a workbook Excel would repair.

## Transactional collaboration

PostgreSQL stores business data, `collab_edit_stacks`, `collab_cell_states` and `collab_outbox` in one transaction. The outbox retries Valkey publication after commit. SQL sessions are request-scoped, not WebSocket-scoped; HTTP mutation dependencies commit before response delivery. See `COLLAB.md` for locks and recovery behavior.

Renovation approval copies the plan with PostgreSQL INSERT SELECT rather than constructing all rows in Python. Submission deletes empty draft rows with SQL predicates. Cell edits batch-load affected rows and serialize updates of the same row to preserve derived calculations. No load-test latency or capacity certification is implied; the planning target is 50 concurrent users across projects.

The renovation plan's 59 formula fields are **stored columns**, recomputed on every edit and read back verbatim by `GET /plan/rows` — recomputing them per read is infeasible (~90 s for the 306k-row plan) and SQL filters/facets need them as columns. The importer skips calculated fields, so imported/adopted rows are filled by `recompute_plan_rows` (`just recompute-renovation-plan`, also run at the end of `just seed-renovation-plan`); see `docs/WORKFLOW.md`. The importer passes the ids of the rows it just wrote, and the recompute writes only rows whose derived values changed: rewriting all 306k rows/133 calculated columns (≈0.95 ms each, ~290 s) was the dominant cost of a ~20k-row load. The full sweep remains the backfill; the import job additionally exposes `parse`/`write`/`recompute` phases and a progress total of twice the preflight row count.

**The renovation ID (`col_003`) is NOT one of them** — the generator is currently off (`RENOVATION_ID_FORMULA_ENABLED = False` in `back/src/renovation/renovation_id.py`): the field is a plain editable text cell, loaded from the template as-is and typed by hand, because the agreement processes and the state of the data made a derived value impractical. `form_schema.CALCULATED_OVERRIDES` / `READ_ONLY_OVERRIDES` drop its legacy «calculated + read-only» flags without touching the legacy JSON entry, so the whole generator (`calculate_renovation_id`, its component validation, the import's correctness check) stays in the codebase and can be switched back on from that one flag. While it is off the import keeps **only the duplicate check** as a blocker («Некорректно рассчитанные коды» is archived in the UI), the «Некорректные» filter means *empty or duplicated*, and submit refuses an empty or repeated code. The «Лот» (`col_189`) code, by contrast, always ends with a dot: a correct code typed or imported without it gets it added silently (`renovation_id.normalize_direction_code`).
