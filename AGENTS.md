# AGENTS.md — ReportCollector

Cheat sheet for agents. Read only the section your task needs; `docs/*` holds the detail.

## What it is

Corporate raw-material planning system («Сырьё»): Excel-like **«Движение»** and **«Цены»** forms + dictionaries (Сырьевые позиции, Активы, Базисы поставки, Пользователи), real-time collaborative editing (cell leases, presence), report-month fixing with a version archive and Excel export.

The «КППК» project (`/kppk`) copies the corporate `raw.kppk_*` source tables
(that data is **not** in the repo — it ships as dumps the operator loads).
Its workspace exposes product positions, raw positions, ДЗО, realization slices
and an admin-only users dictionary. Product/raw numbers are editable and unique;
missing matrix products use an unnumbered `@`-prefixed name. Periods remain
backend-only. Editable tabs use shared collaboration, leases and session-scoped
undo/redo. Realization rows are dictionary-derived (no row deletion). KPPK users
currently have only the administrator assignment; their ДЗО column is empty and
read-only. Other projects' memberships and asset grants must not change when
editing a KPPK membership.

The «Логистика» project (`/logistics`) is role-based: **administrator** (every
tab, every row), **reviewer** (the whole rates table, read-only, no dictionary
tabs), **asset worker** (only the rates of the assets granted in «Пользователи»,
with no «Актив» column — the server stamps their asset) and **blocked** (no
access; the `is_active` flag is derived from the role, and a worker without a
single grant is treated as blocked). Its main table is
«Ставки» (`l_rates`): one row per asset × route × transport per month, stamped
with the first day of the toolbar's month/year selection. The rate rows hold
FOREIGN KEYs into the corporate **`dict`** schema, so that schema is required —
migration `0035_logistics_rates` fails loudly without it (`LOGISTICS_DICT_SCHEMA`,
default `dict`; the tests use their own schema). A physically deleted dictionary
row is refused, a soft-deleted one (`deleted_at`) stays valid and is shown as
«УДАЛЕНО: …». Computed and NOT stored: «Направление» (departure РФ → the
DESTINATION market «РФ»/«БЗ»/«ДЗ»; otherwise destination РФ → «Импорт», else
«Экспорт»), «Опасный» (hazard class set), the dollar
equivalents (`rub / 83.90`, a constant until the source provides the rate) and
the month-over-month deviations (net − the previous month's net of the SAME
asset × route × transport × vehicle; «%» is temporarily the same rub
difference, the division arrives with the real formula). Rows are edited in place and
deleted only by an administrator (confirmed by a dialog; the delete is journaled
so undo restores the row). `l_rates` collaborates like КППК: room
`logisticsRates` per period (any working member), undo scope
`logisticsRates/<period>` (an asset worker undoes only their own assets'
actions). The trailing empty row is a LOCAL draft —
it inserts only once every required field is filled, and the insert uses the
period selected at that moment. The rest of the workspace is three read-only
dictionaries read from `dict` (`back/dicts.sql`): countries (short names + the
computed market РФ/БЗ/ДЗ), the combined transport-type × vehicle-type matrix, and
organization prefixes; its «Пользователи» tab manages the project members (only
the administrator role) and collaborates/undoes like the other projects' users
dictionaries. Nothing in this application writes to `dict` — no
migration creates it and no seeder touches it; a missing table returns a short
built-in demo list with `source="fallback"`, and the grid warns about it. Every
read filters `deleted_at IS NULL` on every joined table. Every logistics grid
exports to Excel through `POST /api/logistics/export` (inline XLSX; the rates
require the period and are scoped by role, «Пользователи» is admin-only).
Migration `0032_logistics` creates the project row and the
`logistics_price_kind` enum; migrations `0033_logistics_hazard_classes` /
`0034_logistics_table_prefix` create and seed the application-owned «Класс
опасности» dictionary (`l_hazard_classes`, values 1..9, no tab yet);
`0035_logistics_rates` creates `l_rates` and its external FKs.
Application-owned logistics tables use the `l_` prefix. `just seed-logistics`
seeds only our side (project, hazard classes, Евтушенко's admin link) and never
touches the external `dict` schema.

- `front/` — Next.js 16.4.6 (App Router, React 19, revo-grid 4.32.0, HeroUI 3.2.8, Tailwind 4, Zod 4), package manager **Bun 1.4.x**.
- `back/` — FastAPI + SQLModel + Alembic (Python 3.15, **uv**), asyncpg, Redis/Valkey for collaboration.
- Ports: front **5000**, back **8000** — from the root `.env` (`FRONT_PORT`/`BACKEND_PORT`), mandatory.
- Docker: `compose.yaml` (profiles `prod`/`dev`); the external services are raised with `just db-stand` (postgres + valkey) and `just auth-stand` (keycloak); the full observability stack is `just monitoring-stand` (loki, grafana, alloy, prometheus, alertmanager); the proxy is `just proxy-stand` (nginx, `fholzer/nginx-brotli` — same build as the central DWH infra stack); in prod the reverse proxy lives in that central stack.

## Commands (from the repo root)

| What | Command |
|---|---|
| Back tests | `uv run --directory back pytest -q` |
| Front unit tests | `bun run --cwd front test` |
| e2e smoke (needs a running stack) | `bun run --cwd front test:e2e --project=smoke` (`E2E_BASE_URL`, `KEYCLOAK_ENABLED=false`); `--project=keycloak` for the separately prepared Keycloak stack |
| Back lint/format/type | `uv run --directory back ruff check --fix` · `ruff format` · `ty check --fix` |
| Front lint/format/type | `bun run --cwd front lint` · `format` · `typecheck` |
| Everything at once | `just lint` · `just typecheck` · `just test` · `just e2e` |
| Stack dev/prod | `just dev` (hot-reload, `--watch`) · `just prod` |
| DB / keycloak | `just db-stand` (postgres + valkey) · `just auth-stand` (keycloak) |
| Observability | `just monitoring-stand` — Loki + Grafana + Alloy (logs), Prometheus + Alertmanager (metrics, alerts). `just monitoring` — Alloy only, against an external Loki. Backend `/metrics`; logs carry `env=<LOG_ENV>` (`.env`, default `local`). Alert mail is **wired and verified** (smarthost 587, `ALERT_SMTP_USER` = the sender mailbox — both load-bearing; see `docs/ARCHITECTURE.md`) |
| Standalone proxy | `just proxy-stand` — reverse proxy (80/443 → front + WS back), same files as the central infra stack |
| Migrations | `just migrate` (never edit already-applied prod migrations — only new ones). The migration connection sets `lock_timeout=30s` — a blocked migration (e.g. an ALTER waiting behind the running app) fails loudly instead of hanging |
| New empty DB / baseline adoption | New: `just migrate`. Legacy head `0015_plan_row_col_218` adoption is **no longer supported after migration 0041** (all `r_plan_rows` columns were renamed/rebuilt; `adopt_baseline` refuses the `col_XXX` schema by design). The live database is on 0040+; details in `docs/WORKFLOW.md` |
| Seed | `just seed` — **fully recreates the DB schema**, don't run without asking |
| КППК seed | `just seed-kppk` — re-copies the `raw.kppk_*` sources (creates them **only when missing**, from `back/src/kppk/data/source_schema.sql` + `demo_source_rows.sql`; the `dict` schema is never touched), rebuilds «Продуктовые позиции» from `back/src/kppk/data/product_matrix.sql` (pairs + «Заполняемый акциз») and **rebuilds the period rows** (see the next line), all in one transaction. The copies resolve the source product/market TEXT into `product_id`/`market_id` uuids and **fail** on an unknown product or market (no silent skip); the market dictionary is topped up to «РФ»/«ДЗ»/«БЗ» if a restored schema left it empty. Text values are normalized on copy and on save (`normalize_text` in `src/kppk/constants.py`): trimmed, single spaces, non-breaking space included — the dictionaries are keyed by NAME, so stray spaces would create a second product. Other projects untouched |
| КППК periods | No separate command: `just seed-kppk` **rebuilds** the «Реализация» (one row per ДЗО × product × market) and «Себестоимость» (per ДЗО × product) rows for every month from **September 2023 to December of the next year**, all values zero. It TRUNCATES those tables first — the seed owns them, like the renovation plan — so a product dropped from the matrix leaves no stale period. The costs are re-derived from the fresh rows afterwards |
| Логистика seed | `just seed-logistics` — seeds only the data this application owns: the `logistics` project row (if missing), the «Класс опасности» dictionary (`l_hazard_classes`, values 1..9) and Евтушенко's active admin link. It does **not** read or write the external `dict` schema, and it does not change his identity or his memberships in other projects. Idempotent |
| Логистика dev/CI `dict` | `just seed-dev-dict` — **locally or in the isolated CI database only, never in prod.** Creates the five external `dict` tables the «Ставки» table depends on (`countries`, `orgs`, `transport_types`, `vehicle_types`, `transport_types_vehicle_types`) **only if missing**, and inserts a demo set with fixed UUIDs (`ON CONFLICT DO NOTHING`). Nothing is dropped or altered; «Морской» deliberately has no vehicle mappings. Required because migration `0035` and every rates request read `dict` — a database without it fails loudly, so CI seeds it BEFORE `migrations.upgrade`. Before loading the real dumps, the tables this script created must be dropped manually |
| КППК source data | The **original** `raw.kppk_*` data is not in the repository: the operator loads the SQL dumps. What the repo does keep (`back/src/kppk/data/`) is the structure, the demo rows for a database without sources, and the product × ДЗО matrix with its excise flags — the only input the dumps never carried |

## Tooling

The commands above are the contract — use them as written. **If one of the required tools is missing, stop and ask the user to install it; never substitute an equivalent.** `python -m pytest` instead of `uv run pytest` silently picks another interpreter (and skips the locked versions); `npx` instead of `bunx` picks another resolver. The result looks fine and is not what CI runs.

- **Required:** `uv`, `bun`, `docker`, `git`, `just` — the table above and `justfile` do not work without them.
- **Available, prefer them over hand-rolled code:** `rg` (ripgrep — the same binary the built-in `grep`/`glob`/`read` tools are built on; inside a session those tools are usually the better route because they avoid quoting and shell-layout traps, but an `rg` invocation in a pipeline is perfectly fine). `jq` / `yq` / `gh` — optional; their absence never blocks a task.
- **MCP servers** — `.mcp.json` (root, committed) declares **three**, all launched through `bunx`, so `bun` is a hard prerequisite: `heroui-react` (HeroUI **v3** component docs/source — the frontend is on v3; v2 examples are wrong), `next-devtools` (Next.js runtime tooling: docs for the installed version, dev-server inspection) and `browsermcp` (controls the user's Chrome — browser automation, only through the `browsermcp` tool from `execute`). Use the framework servers for framework questions instead of guessing from memory. If a server is unavailable, say so and ask the user — do not fall back on recollection.
- **HeroUI v3 docs (the MCP's published source)** — the same documentation is served for agents: `https://heroui.com/react/llms.txt` is the index of every page, `https://heroui.com/react/llms-full.txt` is all of them in one file (~5.8 MB — fetch a specific page from the index, or download the dump to a temp file and `rg` it; never load it whole), and `https://heroui.com/react/DESIGN.md` is the theme/visual-language guide. These are **v3-only**, exactly like the MCP: v2 pages (`v2.heroui.com`) and v2 examples do not apply to this frontend.
- **Live-stack login is fixed:** `evtushenko` / `evtushenko` — the seeded test administrator with access to every project. Never guess or probe credentials; `admin/admin` is the Keycloak admin console, not an app account. Details — `docs/KEYCLOAK.md`.

**Do not write throwaway helper scripts** (a bespoke JSON parser, a file-walker, a search loop) when one command or one built-in tool does the job. Ask first — a helper left in the tree is untracked work that the next agent has to discover and delete.

**Cyrillic does not survive the PowerShell pipeline.** The data is fine — `psql` prints «Российская Федерация» correctly — but a value that passes *through* the shell comes back as mojibake (`Выкуп` → `��룯`), and a `Select-String`/`-replace` built on such output will miss or corrupt it. Consequences:

- read and edit **files** containing Russian with the `read`/`edit`/`write` tools, never with shell text replacement — a bulk Cyrillic `Substring`/`-replace` silently rewrites the wrong bytes;
- when a shell command's output looks garbled, the command may still have succeeded: re-read the result with a tool (`read`, or a fresh `psql` call writing to a file) before "fixing" anything;
- for comparisons of Russian labels, prefer a query that returns a boolean/count (`SELECT count(*) … WHERE name = :v`) over eyeballing printed text.

## Landmines (critical!)

1. **`AUTH_APP_ORIGIN` — no trailing slash.** compose derives the CORS origins from it; the browser never sends a slash in `Origin` → the WS handshake fails with 403. `back/src/config.py` normalizes slashes, but the correct `.env` has none.
2. **Programmatic selection in revo-grid — only `grid.setCellsFocus(start,end)` + `grid.refresh()`.** `SelectionStore.setRange` is a dead API (in `revogr-overlay-selection` there's a raw observable, no such method): the call silently crashes the handler. Reset — `grid.clearFocus()` **also needs `grid.refresh()`**, and **`grid.pendingColumnFocusRestore = undefined`**: after any `columns` change while a range was active, `columnChanged` caches the focus and `restoreColumnFocusAfterRender` re-applies a single-cell range on the next render — its "column didn't move" guard reads the store, which `clearFocus()` just emptied, so it can't match. Row selection must clear the range **before** `setSelectedRowIds` (selectedRowIds is a `columns` dependency → the rebuild captures the live focus), or the border resurrects after the click.
3. **Sorting/filtering: screen index ≠ `rows` array index.** Any "index → row" mapping (paste, cut, copy, presence, `canPasteRange`, row shift-select, Ctrl+A) must go through `getVisibleRows()` = `grid.getVisibleSource("rgRow")`. Otherwise values are committed to the wrong row → fake asset "deletions" → FK RESTRICT.
4. **Excel paste:** trim trailing empty rows/columns from the buffer (Excel puts a trailing `\n`) — otherwise an extra empty cell below overwrites a neighbor.
5. **Hotkeys — by `event.code` (`KeyC`/`KeyV`/`KeyX`/`KeyA`/`KeyZ`/`KeyY`/`Delete`), not `event.key`** — otherwise broken with the Russian layout. Ctrl+Delete opens the delete dialog (all dictionary grids + the renovation plan). On the «Ремонт» plan **Ctrl+A is fully disabled** — the form holds ~94k rows, a whole-form copy would overflow the clipboard; the key is swallowed, revo-grid's built-in select-all included.
6. **Emails — lowercase only and through validators:** back — `EmailStr`/`email_validator`, front — `z.email()`. Both **reject special-use domains** (`.local`, `.localhost`, `example.com`) — use `@example.ru` in tests. Case-insensitivity: lookup by `lower(email)`, Keycloak finds the existing user.
7. **The business plan is edited only in «Цены»** (`businessPlanQuantityTons` etc.); in «Движение» the `businessPlanTons` column is a projection, non-editable. Don't "project" edits between forms.
8. **Fixing («Зафиксировать»)** — only the first open month and with no active leases (the all-cells-filled requirement was removed 2026-09-04: a month may be fixed as-is); the preliminary version is overwritten, the final one is immutable; a final fix opens the next month and **clears the fixed movement scope's undo/redo history**.
9. **Undo/redo is scoped per session and form** (PostgreSQL `collab_edit_stacks`: user/session/scope): «Цены» history doesn't undo «Движение». The `session_id` is a page-lifetime id the frontend sends with every edit/undo call (`getCollabSessionId()` in `ws-client.ts`) — a page reload starts with empty history. Don't drop it from PATCH/undo/redo calls (backend requires it).
10. **Presence vs lease:** lease (TTL 20s, renew 10s) — the user is editing; presence (TTL 25s, heartbeat 15s) — the user is "online", including a completed selection. Idle >2 min or hidden tab → leases released, heartbeat stops; on return — re-join via ping.
11. **Archive «Движение» version — read-only**: no editing and no presence.
12. **«Пользователи» dictionary & self-provisioning:** an admin adds a person — they appear in all projects (in others `is_active=false`). A user can also self-register into ONE project by opening its URL (`/raw-materials`, `/renovation`, `/kppk`) — an inactive link is created there; the root URL never provisions anyone (GET `/api/me` runs with create=False), and the first successful Keycloak login fills in the data (FIO overwritten from Keycloak).
13. **revo-grid 4.32.0 `afteredit` carries `data` on plain single-cell edits too** — the whole source snapshot as an ARRAY. A range save (our `rangeeditapply` path) carries a keyed patch object + `models` instead. `extractEdits` (both grids) must tell them apart by shape (`data` is a keyed object AND there is no `prop` = range save), not by `data` presence — otherwise every cell edit silently produces no updates: the grid shows the new value locally, but nothing reaches the server.
13a. **The Excel load (`POST /plan/import/apply`) returns `202` and a durable job.** Accepted containers are `.xlsb` (`pyxlsb`) and `.xlsx` (`openpyxl`, `read_only=True, data_only=True`): the reader is chosen by the upload's own suffix, and the durable input key keeps it. An isolated child streams batches of 200 and commits the plan writes and job result in ONE transaction, fenced by the job lease. Reference labels and assets resolve strictly to existing catalog entries; validation failure rolls back the whole file. Rows match by «Код работы»; unknown codes append, blank codes never clear the plan. The load recomputes only the rows it wrote (`recompute_plan_rows(row_ids=…)`); the full sweep stays the backfill. Never add per-row ORM flushes or undo journaling. The shared project gate spans writes/recomputation but does NOT serialize edits; Gate A blocks the form through `GET /plan/import/active` and a 409 write guard. Stale cell leases and staged finalization remain open. Details — `docs/DATA-JOBS.md`, «Loading the checked file».
14. **The document never scrolls.** `html` is `height: 100%; overflow: hidden` and `body` is `height: 100%` with **`overflow: visible`** (`globals.css`; `layout.tsx` body is `h-full`, no `min-h-full`). The app is a fixed shell (`.app-shell: 100dvh`) that scrolls internally. HeroUI portals overlays (tooltips, popovers) into `document.body` as absolutely-positioned boxes — a portal anchored near the footer (the action bar's floating tooltips) would otherwise grow the document and flash a stray page scrollbar. Clip only `html`: giving `body` `overflow: hidden` too would **cut off** those body-owned portals (the tooltip disappears). The floating action tooltips additionally pin themselves with `style={{ position: "fixed" }}` so they never grow the document.
15. **Excel is unforgiving about streamed workbook XML and streamed ZIP bytes (`back/src/common/xlsx_stream.py`).** Content-level: a duplicate `<mergeCell ref>`, a `<row r>` out of order, **or an empty `<mergeCells count="0">` block** → repair dialog («файл повреждён»); the writers therefore emit the totals row canonically in `__init__` BEFORE the data, merge ranges are deduplicated first-writer-wins (`merge_cells_xml` returns an empty string for a mergeless sheet — never an empty block), and the writer's `row_total` promise fails fast with `ExportRowCountMismatchError` (the job drops its `.part`). A `<dimension ref>` element is required by openpyxl's read-only mode (`max_row` is None without it) — `sheet_prefix_xml(dimension=…)` writes it. Formula templates carry a `\x00` row placeholder that must be escaped with plain `xml_specials_escape` (`&<>` only) — `xlsx_stream.xml_escape` strips NUL and silently breaks the exported derived-column formulas. Container-level (cost a full 520 MB production export): `zipfile` on a **seekable** sink writes a draft local header (zero CRC/sizes) and patches it by seeking back at `close()`; the download streamer (`stream_growing`) tails the growing `.part`, so the browser receives the draft header bytes and never the patch — the downloaded archive is born invalid (Excel: «проверка на уровне файла» → repair). The writers hence wrap their sinks in `xlsx_stream.UnseekableSink` (append-only → data-descriptor entries, bit 3), and stay on **plain ZIP, not zip64**: a streamed zip64 entry carries an all-zero zip64 extra field that real Excel rejects outright (COM-verified; descriptors without zip64 open clean). Any byte-served archive must keep both invariants; a part > 4 GiB would need a buffered (non-tailed) write path. Full rationale and the verified cases — `docs/GRID.md` («Misc») and the module docstrings.
16. **`r_plan_rows` uses semantic names, not `col_NNN` (migration 0041).** `renovation-form.json` `dbName`/`formula` and the ORM/API/grid all use the §9.1 English names; PHYSICAL column order equals the form order (table rebuild, `estimate_number` at position 31). The only surviving `col_*` is `col_002` (synthetic «Предприятие» → `asset_id`/`__assetName`). Never introduce a new `col_XXX` reference: PostgreSQL truncates identifiers to 63 bytes and 88 dictionary names were shortened by the approved rule. Old saved filter/sort/pinned/hidden keys are decoded on read via `decodeLegacyFieldName` in `front/src/shared/api/renovation-shared.ts` (re-exported by the `renovation.ts` barrel). Details — `docs/GRID.md`, «Semantic field names (migration 0041)».

## Working rules

- **Branches — mandatory, one logical task per branch.** Create `feat/<short>` / `fix/<short>` / `docs/<short>` from current `dev` BEFORE the first edit; do the whole task there. Start the next task only after the previous one is merged into `dev` (`git merge --no-ff`); squash is reserved for `dev → main`. Details — `docs/WORKFLOW.md`, «Branching and commits» / «Merge checklist».
- **Semantic parts:** one logical change per commit, each with its own Why/What/Verified. A task spanning backend + frontend + docs is ONE branch but several atomic commits. Details — `docs/WORKFLOW.md`.
- **When in doubt, ask the maintainer — do not guess.** Ambiguous product/config/process decisions (semantics, versions, naming, what to commit) → stop and ask; only safe, reversible technical choices may proceed with documentation. Missing required tool → ask to install and stop; red checks → don't report done; foreign/untracked changes → never discard. Details — `docs/WORKFLOW.md`, «Working principles».
- **Definition of done:** quality checks + CI green, behavior verified by hand where tests can't see it, docs updated in the same change, no debug artifacts/secrets, one logical commit each, every ambiguity asked. Full checklist — `docs/WORKFLOW.md`, «Definition of done».
- **One home per fact:** this file is a **thin index**; detail lives in `docs/`. Keep one line plus a pointer here, never a full copy; record landmines immediately in the matching `docs/` section, not by growing this index. Details — `docs/WORKFLOW.md`, «Working principles».
- **Git:** root `.gitignore` — deny-all + explicit whitelist (`back/`, `front/`, `docs/`, `infra/`, `.github/` etc. restored as whitelists in their own files). A new root file must be explicitly whitelisted. Nested `back/.gitignore`, `front/.gitignore` are whitelist-style too. Before any git operation check `git status` and the branch; never discard foreign or untracked work.
- **Don't commit debug files:** `back/tests/debug_*.py`, `front/e2e/*-probe*`, `front/e2e/probe/` (the root ignore already silences `*.mjs`/`*.cjs`). Delete them after debugging.
- **A11y label rule:** a form control without a visible `Label` must have an `aria-label` — react-aria otherwise warns on every mount and floods dev logs (`docker logs`). Details: `docs/WORKFLOW.md`.
- **Front styling:** semantic, recurring UI — plain CSS classes in `front/src/app/globals.css` on theme vars; one-off layout and HeroUI slot overrides — Tailwind utilities inline; one pattern = one vocabulary. Details: `docs/WORKFLOW.md`.
- **Tabs:** in every admin workspace the strip is ordered **main tables <splitter> dictionaries <splitter> users** (`WorkspaceTab.separatorBefore`) — «Логистика» starts with «Ставки», then the dictionaries and the users. Details: `docs/GRID.md`, «Misc».
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes` (normalizes to LF on add/checkout, overrides `core.autocrlf`) and prek hooks (`mixed-line-ending --fix=lf`, `fix-byte-order-marker`). Details: `docs/WORKFLOW.md`.
- **Commits:** conventional commits `type(scope): …` + body Why/What/Verified, one logical change per commit (template, branch lifecycle and merge rules — `docs/WORKFLOW.md`). The user commits themselves; don't commit without an explicit request.
- **Before delivery:** typecheck + lint + tests on both sides (see the table). Backend tests recreate a dedicated `rc_test*` PostgreSQL schema through Alembic. CI runs the full suite against both MemoryStore and real Valkey. Real-store local runs require a disposable Valkey DB 15 and explicit reset opt-in; see `docs/WORKFLOW.md`. Never point the reset-enabled fixture at shared services.
- **Sync frontend/backend changes into a running dev stack by restarting it with `just dev`** (compose `--watch` syncs `front/src`/`back/src`), **not by `docker cp` into `rc-front-dev`/`rc-back-dev`** — docker cp leaves the container out of sync with the repo and silently diverges from what a fresh checkout builds. If `just dev` is already running someplace, its watch picks the edits up on save; otherwise start it. Don't hand-copy files into containers to "test fast".
- **e2e:** don't run without the full stack (Next + back + Postgres + seed, `KEYCLOAK_ENABLED=false`); workers=1, chromium.
- **Caching reads (`back/src/common/read_cache.py`):** cache a table only when it is read on nearly every request AND has a cheap token of its own change (`count(*)` + `max(updated_at)`) — never a timer. Cached today: the renovation reference catalogue and КППК `read_excise_products` (both sit in per-request paths). **Deliberately not cached:** the «Сырьё» and КППК dictionaries — they are read once per page load, so a cache would buy nothing and only add a staleness risk, and the plan's row counts were tried and reverted (a correct token cost more than the count). The tests clear the cache per test (`conftest.py`), since every test recreates the schema.
- **Languages:** code comments/docstrings, commits and this documentation — English; user-facing strings (UI labels, toasts, API errors) — Russian. See the "Languages" section in `docs/WORKFLOW.md`.

## Documentation

- `docs/ARCHITECTURE.md` — system diagram, environment variables, raw-materials data model, API, auth, deploy, implemented invariants.
- `docs/DATA-JOBS.md` — renovation import/preflight and durable import/export jobs, streaming XLSX and artifact visibility.
- `docs/DOMAINS.md` — КППК and Логистика data models.
- `docs/RENOVATION-DESIGN.md` — renovation service/router/API module boundaries and measured query/facet cost.
- `docs/COLLAB.md` — WebSocket protocol, leases, presence, undo/redo, locks.
- `docs/GRID.md` — revo-grid: forms and columns, clipboard, selection, coordinates, tooltips; the plan sections are split by topic (`###`), so jump to the heading.
- `docs/TODO.md` — single current engineering backlog and explicit product deferrals.
- `docs/LIMITS.md` — Phase 9 inventory and classification of every resource limit (inventory only, owner decisions pending).
- `docs/WORKFLOW.md` — environment, quality, languages standard, pre-commit (prek), branching/commits/merge, Definition of done, working principles (the canonical process file).
- `docs/KEYCLOAK.md` — corporate Keycloak: OIDC flow, prod config, claims→user, provisioning, local stand, diagnostics. Prod artifacts — `docs/keycloak/` (OIDC discovery, access token example).
- `docs/samples/renovation_td_example.xlsb` — a real technical-directorate plan (~48 KB, committed): the template the plan importer is matched against, and what `back/tests/test_plan_import.py` runs on. Replace it when the customer's template changes.
- **Historical, not the current contract** (read only for background): `docs/RENOVATION_MIGRATION_CONTEXT.md` — it opens with a status note saying which passage is outdated.

**When behavior changes — update the corresponding file in `docs/`.** That's a requirement, not a wish: the docs are the only source of context for future agents.
