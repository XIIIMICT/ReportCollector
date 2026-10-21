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
     ── Valkey/Redis (leases, presence, pub/sub; VALKEY_URL empty → in-memory)
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
and column in Russian (30 tables, 495 columns). Renovation plan column comments
reuse the complete normalized header hierarchy shown by the form selection
status. Source schemas `raw.*`/`dict.*` and `alembic_version` are excluded.
`back/src/common/data_catalog.py` also attaches comments when each model module
registers its tables, including tables recreated by the KPPK seeder. The
migration carries a frozen snapshot, not runtime imports; future comment
changes need a new migration. Legacy baseline adoption tolerates absent
catalog comments deferred to 0014, but still rejects unexpected existing
comments and structural drift. Legacy KPPK fields not exposed by the form
retain source-field descriptions; undocumented units are not inferred.

## Data model (project «КППК», key `kppk`)

Phase 1 copies the source tables of the corporate КППК database into the program
schema and exposes the four dictionaries the «Таблицы.xlsx» phase builds on.
Sources are the dumps in `docs/kppk/` (`ddl.sql` + `kppk_*_<timestamp>.sql` +
«Матрица продуктов.xlsx»).

- **Source schemas** `raw` (`raw.kppk_d_org`, `raw.kppk_d_period`,
  `raw.kppk_d_product_seq`, `raw.kppk_d_raw_seq`, `raw.kppk_f_realization`,
  `raw.kppk_f_realization_plan`) and `dict` — created + filled by the seeder
  **only when missing**; on prod installations they already exist and are never
  touched.
- **Program copies** (`back/src/kppk/models.py`, prefix `kppk_`): `kppk_orgs`,
  `kppk_periods`, `kppk_products`, `kppk_raws` (the dictionaries),
  `kppk_org_products` («Продуктовые позиции»: product × ДЗО links from the
  matrix), and the verbatim read-only `kppk_realization` /
  `kppk_realization_plan` copies for the next phase. Every copy is uuid-keyed;
  the source integer id stays as a unique column (`org_id`, `period_id`,
  `product_num`, `raw_num`) — the seeder's re-copy key and the join key the
  fact/plan copies reference. `product_num`/`raw_num` are admin-editable in the
  UI and stay unique; the dictionary names are unique as well
  (`kppk_orgs.code`/`name`, `kppk_products.name`, `kppk_raws.name`,
  `kppk_periods` by `(month_name, year_num)`); several NULL source numbers stay
  allowed (PostgreSQL treats NULLs as distinct) — the matrix brings in products
  without a number.
- **Seeder** `just seed-kppk` (`back/src/scripts/seed_kppk.py`, one
  transaction): re-copies the source tables on every run (truncate + re-insert,
  audit stamps on the four dictionaries) and rebuilds `kppk_org_products` from
  the matrix; products and ДЗО are matched by exact display name — no
  normalization (spaces stay significant). The ДЗО copy keeps the source id,
  code and name only — `raw.kppk_d_org.time_zone` is not copied. A matrix
  product missing in the source dictionary is created without a `product_num`
  under an `@`-prefixed name; a dictionary product missing in the matrix keeps
  an empty ДЗО. The `kppk` project row and its user links are owned by
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

**The active form is URL state.** `?section=<form id>` on a project URL pins the open form, so a reload (and browser back/forward) lands on it instead of the default. The server page `front/src/app/[project]/page.tsx` reads `searchParams.section` and passes it as `initialSection`; each workspace restores it (renovation: `plan` / `assets` / a catalog id / `users`; raw materials: `movement:{period}` / `rawMaterialPrices` / a dictionary), validates it against its own tabs and falls back to the first one when unknown (notifying the URL so a stale value is corrected). A change is mirrored back with `router.replace(..., { scroll: false })` — `replace`, not `push`, keeps the back button on the previous page. Switching project (`router.push(slug)`) drops the parameter, since form ids are project-specific. Nothing else is persisted: filters, sorting, the selected version and the report month are not saved.

**Identity between front and back** — signed headers `X-ReportCollector-*` (email, name, sub, issuer, preferred-username, … + timestamp + HMAC signature over `AUTH_SECRET`): `front/src/shared/auth/identity-headers.ts`, verified in `back/src/auth/service.py::verify_signed_identity`. All `/api/*` front routes (App Router) proxy to back via `front/src/shared/api/backend-proxy.ts` (`/api/raw-materials/[...path]`, `/api/auth/*`, `/api/me`, etc.). The browser can't set custom headers on WebSocket — for WS there's a one-shot **ticket** (signed query parameter): `verify_identity_ticket`.

## API (back, prefix `/api`)

- `auth`: `POST /auth/login`, `POST /auth/users/{id}/password`, `GET/PATCH /me`.
- `raw-materials` (all require a signed identity):
  - Reads: `GET /meta`, `/movement?report_month=`, `/movement/meta`, `/movement/snapshot/{id}`, `/prices`, `/raw-materials`, `/assets`, `/delivery-bases`, `/users`.
  - Edits: `PATCH /movement`, `PATCH /prices` (cell batches → `cell:changed` into the room), dictionary CRUD (admin).
  - Undo/redo: `GET /undo-state?section=&report_month=`, `POST /undo`, `POST /redo` (scope in the body).
  - Fixing: `POST /movement/fix` (admin) — see "Landmines" in AGENTS.md.
  - `POST /export` — Excel (openpyxl): whole/visible selection, for «Движение» — archive versions.
  - Renovation plan export — **background jobs**: `POST /plan/export/jobs` (202 + job id), `GET /plan/export/jobs/{id}` (status/progress), `GET /plan/export/jobs/{id}/file` (download). The legacy synchronous `GET/POST /plan/export` endpoints remain for tests/compat but the UI never uses them (they materialize the whole plan in the API process).
- `kppk` (signed identity; reads for every project member, mutations admin-only): dictionary CRUD — `GET/POST/PATCH/DELETE /product-positions[/{id}]` (PATCH `{field, value}`, `field` = `name` | `orgNames` | `productNum`; create accepts `productNum`; delete removes the product with its links), `/raws` (PATCH `{field, value}` for `name`/`rawNum`), `/orgs` (PATCH `{field, value}` for `code`/`name`), `/periods` (PATCH `{field, value}`; no UI in this phase). The source number («№», `productNum`/`rawNum`) is editable and unique — an occupied number is rejected with a clear `400`; `null` stands for a row added by hand (several NULLs are allowed); positions/raws are returned in the source-number order (unnumbered rows last). The «ДЗО» list is ordered by the full name and exposes no number. Undo/redo join the shared collaboration journal: every mutation carries the page-lifetime `session_id` (query) and is journaled as one action scoped to the tab (`scope = f"{section}/"`, no report month; `collab/undo.py` dispatches `kppk_products`/`kppk_raws`/`kppk_orgs` to `apply_kppk_cell_value`) — `GET /undo-state?section=&session_id=`, `POST /undo`, `POST /redo` (body `{skipActionId, section}`, admin; no WebSocket room and no `cell:changed` — the front refreshes the grid itself after undo/redo). `POST /export` streams the tab as XLSX (whole/visible via `rowIds`, `columns` from the grid). Deletes follow RESTRICT: a ДЗО/period referenced by the fact/plan copies is rejected with a clear `400`.
- `ws`: `WS /ws/raw-materials` — collaboration (see `docs/COLLAB.md`).
- Service: `GET /health` (process liveness), `GET /ready` (PostgreSQL/Valkey readiness, including the required outbox migration), rate-limit and log middlewares in `back/src/main.py`. HTTP responses carry `X-Request-ID`, also recorded in structured request logs. Uvicorn access/handshake logs redact credential query parameters (`ticket`, `access_token`, `token`); the external reverse proxy must apply its own redaction policy.

Errors: `CellValidationError` → 422, `DictionaryReferenceError` → 409, `ValueError` (not found) → 404, no access → 403. Clear Russian `detail` messages — the front shows them via `readApiDetail` (`front/src/shared/api/raw-materials.ts`).

## Logging

`back/src/app_logging.py`: structured events (`application_started`, `http_request`, `collab.ws_*`, `keycloak_identity_received`, …) to the `LOG_FILE` file and to Loki via Alloy; viewing — Grafana (`{app="reportcollector"}`). No tokens/secrets in logs.

## Deployment (prod)

nginx is **not in Docker**, config `/etc/nginx/conf.d/reportcollector.conf`: `location /` → front `127.0.0.1:5000`; `location /api/ws/` → back `127.0.0.1:8000` with `Upgrade`/`Connection "Upgrade"` and large timeouts (`proxy_read_timeout 86400s`). HTTPS/WSS terminated by nginx (cert in SAN). Prod address: `https://m2v-form-1.h2-group.ru`. Details and WS diagnostics — `docs/COLLAB.md`, "Deployment and diagnostics" section.

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
first byte). The plan export runs its own specialized generator on the same
shared `ExportJobManager` queue; both workers start in the app lifespan.

A full plan export is ~306k rows × 216 columns: minutes of CPU and ~500 MB of
XLSX (the sheet XML exceeds 4 GB uncompressed → ZIP64). Doing that in the
request handler froze the whole event loop for every user and several parallel
requests OOM'ed the container. The export is therefore a background job
(`back/src/renovation/export_jobs.py`):

- `POST /plan/export/jobs` resolves the scope (plan + asset + block + header
  filters + optional rowIds), computes the export stamp (last row edit for the
  working plan) and enqueues a job; the response is immediate. An identical
  scope+stamp reuses the ready file (cache key = sha256 of the scope + stamp).
- A single sequential worker (started in the lifespan, in-process — the app runs
  one uvicorn worker) streams the rows through a server-side cursor and writes
  the XLSX to `EXPORT_DIR` (default: the system temp dir). Formula
  materialization and XML serialization run via `asyncio.to_thread`, so the API
  stays responsive (measured during a full export: avg ~140 ms, max <600 ms).
- **Throughput.** The row read selects the `col_*` value columns directly; the
  earlier `jsonb_strip_nulls(to_jsonb(row) - keys)` sparse-values build measured
  ~2 ms/row on the 306k-row plan (~10 of the 13 minutes), the direct read
  ~0.1 ms/row. The formula engine now runs only for rows that miss a calculated
  value (recalc happens on edit; adopted rows may never have run it), and the
  ~130 per-row Excel formula templates are XML-escaped once at layout build time
  instead of per cell — the full export went from ~13 min to ~1.7 min. The
  workbook sets `fullCalcOnLoad`, so Excel recalculates on open.
- Limits: 8 active jobs globally, 2 per user → `429`; finished jobs and files
  are pruned after an hour. Completed jobs survive as cache entries; jobs are
  in-memory, so a restart drops pending ones (the client re-creates).
- The workspace starts the job and then hands the file URL to the browser as a
  **native download** (an `<a download>` click on `/plan/export/jobs/{id}/file`).
  The endpoint **streams the growing file** (`ExportJobManager.stream_growing`): it
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

The renovation plan's 59 formula fields (and the `col_003` renovation ID) are **stored columns**, recomputed on every edit and read back verbatim by `GET /plan/rows` — recomputing them per read is infeasible (~90 s for the 306k-row plan) and SQL filters/facets need them as columns. The importer skips calculated fields, so imported/adopted rows are filled by `recompute_plan_rows` (`just recompute-renovation-plan`, also run at the end of `just seed-renovation-plan`); see `docs/WORKFLOW.md`.
