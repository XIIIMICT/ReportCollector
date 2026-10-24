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

- `front/` — Next.js 16 (App Router, React 19, revo-grid 4.27, HeroUI, Tailwind 4, Zod 4), package manager **Bun 1.4.x**.
- `back/` — FastAPI + SQLModel + Alembic (Python 3.14, **uv**), asyncpg, Redis/Valkey for collaboration.
- Ports: front **5000**, back **8000** — from the root `.env` (`FRONT_PORT`/`BACKEND_PORT`), mandatory.
- Docker: `compose.yaml` (profiles `prod`/`dev`/`standalone`); in prod nginx runs **outside** Docker.

## Commands (from the repo root)

| What | Command |
|---|---|
| Back tests | `uv run --directory back pytest -q` |
| Front unit tests | `bun run --cwd front test` |
| e2e smoke (needs a running stack) | `bun run --cwd front test:e2e --project=smoke` (`E2E_BASE_URL`, `KEYCLOAK_ENABLED=false`); `--project=keycloak` for the separately prepared Keycloak stack |
| Back lint/format/type | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` |
| Front lint/format/type | `bun run --cwd front lint` · `format` · `typecheck` |
| Everything at once | `just lint` · `just typecheck` · `just test` · `just e2e` |
| Stack dev/prod | `just dev` (hot-reload, `--watch`) · `just prod` |
| DB / keycloak | `just db` · `just keycloak` (standalone profile) |
| Migrations | `just migrate` (never edit already-applied prod migrations — only new ones). The migration connection sets `lock_timeout=30s` — a blocked migration (e.g. an ALTER waiting behind the running app) fails loudly instead of hanging |
| New empty DB / baseline adoption | New: `just migrate`. Existing old head `0015_plan_row_col_218`: backed-up, stopped installation + `uv run --directory back python -m migrations.adopt_baseline` once, then `just migrate`. Guarded schema comparison; details in `docs/WORKFLOW.md` |
| Seed | `just seed` — **fully recreates the DB schema**, don't run without asking |
| КППК seed | `just seed-kppk` — re-copies the `raw.kppk_*` sources (creates them **only when missing**, from `back/src/kppk/data/source_schema.sql` + `demo_source_rows.sql`; the `dict` schema is never touched), rebuilds «Продуктовые позиции» from `back/src/kppk/data/product_matrix.sql` (pairs + «Заполняемый акциз») and **rebuilds the period rows** (see the next line), all in one transaction. Text values are normalized on copy and on save (`normalize_text` in `src/kppk/constants.py`): trimmed, single spaces, non-breaking space included — the copies are keyed by NAME, so stray spaces would create a second product. Other projects untouched |
| КППК periods | No separate command: `just seed-kppk` **rebuilds** the «Реализация» (one row per ДЗО × product × market) and «Себестоимость» (per ДЗО × product) rows for every month from **September 2023 to December of the next year**, all values zero. It TRUNCATES those tables first — the seed owns them, like the renovation plan — so a product dropped from the matrix leaves no stale period. The costs are re-derived from the fresh rows afterwards |
| КППК source data | The **original** `raw.kppk_*` data is not in the repository: the operator loads the SQL dumps. What the repo does keep (`back/src/kppk/data/`) is the structure, the demo rows for a database without sources, and the product × ДЗО matrix with its excise flags — the only input the dumps never carried |

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
13. **revo-grid 4.27 `afteredit` carries `data` on plain single-cell edits too** — the whole source snapshot as an ARRAY. A range save (our `rangeeditapply` path) carries a keyed patch object + `models` instead. `extractEdits` (both grids) must tell them apart by shape (`data` is a keyed object AND there is no `prop` = range save), not by `data` presence — otherwise every cell edit silently produces no updates: the grid shows the new value locally, but nothing reaches the server.
13a. **The XLSB load (`POST /plan/import/apply`) is strict, batch and not journaled.** Reference labels resolve **only** to existing form-catalog options (a label like «ОКР»/«ДО»/«Собственными силами» aborts the whole file with `409` — nothing written); the **asset** follows the same rule (`unknown_assets` — a code outside the project's dictionary refuses the file instead of skipping its rows); reference/numeric cells are normalized through `normalized_option` (`3.0` → `3`), shared by the analyzer and the writer so both flag the same cells. Blank required cells go to `blank_values`, and a blank «Код работы» means the row is not loaded (never "clear the plan"). Rows are matched by «Код работы»; unknown codes append. Parsing runs in a worker thread, writes go in batches of 200 inside ONE transaction under `lock_project` — **never** add a per-row ORM flush (216 columns → one round trip each) and **never** journal the load into undo/redo. Known risk: a user holding a lease on a cell of a rewritten row can still commit a stale value afterwards. Details — `docs/ARCHITECTURE.md`, «Loading the checked file».
14. **The document never scrolls.** `html` is `height: 100%; overflow: hidden` and `body` is `height: 100%` with **`overflow: visible`** (`globals.css`; `layout.tsx` body is `h-full`, no `min-h-full`). The app is a fixed shell (`.app-shell: 100dvh`) that scrolls internally. HeroUI portals overlays (tooltips, popovers) into `document.body` as absolutely-positioned boxes — a portal anchored near the footer (the action bar's floating tooltips) would otherwise grow the document and flash a stray page scrollbar. Clip only `html`: giving `body` `overflow: hidden` too would **cut off** those body-owned portals (the tooltip disappears). The floating action tooltips additionally pin themselves with `style={{ position: "fixed" }}` so they never grow the document.
15. **Excel is unforgiving about streamed workbook XML and streamed ZIP bytes (`back/src/common/xlsx_stream.py`).** Content-level: a duplicate `<mergeCell ref>`, a `<row r>` out of order, **or an empty `<mergeCells count="0">` block** → repair dialog («файл повреждён»); the writers therefore emit the totals row canonically in `__init__` BEFORE the data, merge ranges are deduplicated first-writer-wins (`merge_cells_xml` returns an empty string for a mergeless sheet — never an empty block), and the writer's `row_total` promise fails fast with `ExportRowCountMismatchError` (the job drops its `.part`). A `<dimension ref>` element is required by openpyxl's read-only mode (`max_row` is None without it) — `sheet_prefix_xml(dimension=…)` writes it. Formula templates carry a `\x00` row placeholder that must be escaped with plain `xml_specials_escape` (`&<>` only) — `xlsx_stream.xml_escape` strips NUL and silently breaks the exported derived-column formulas. Container-level (cost a full 520 MB production export): `zipfile` on a **seekable** sink writes a draft local header (zero CRC/sizes) and patches it by seeking back at `close()`; the download streamer (`stream_growing`) tails the growing `.part`, so the browser receives the draft header bytes and never the patch — the downloaded archive is born invalid (Excel: «проверка на уровне файла» → repair). The writers hence wrap their sinks in `xlsx_stream.UnseekableSink` (append-only → data-descriptor entries, bit 3), and stay on **plain ZIP, not zip64**: a streamed zip64 entry carries an all-zero zip64 extra field that real Excel rejects outright (COM-verified; descriptors without zip64 open clean). Any byte-served archive must keep both invariants; a part > 4 GiB would need a buffered (non-tailed) write path. Full rationale and the verified cases — `docs/GRID.md` («Misc») and the module docstrings.

## Working rules

- **Git:** root `.gitignore` — deny-all + explicit whitelist (`back/`, `front/`, `docs/`, `infra/`, `.github/` etc. restored as whitelists in their own files). A new root file must be explicitly whitelisted. Nested `back/.gitignore`, `front/.gitignore` are whitelist-style too.
- **Don't commit debug files:** `back/tests/debug_*.py`, `front/e2e/*-probe*`, `front/e2e/probe/` (the root ignore already silences `*.mjs`/`*.cjs`). Delete them after debugging.
- **A11y label rule:** a form control without a visible `Label` must have an `aria-label` — react-aria otherwise warns on every mount and floods dev logs (`docker logs`). Details: `docs/WORKFLOW.md`.
- **Front styling:** semantic, recurring UI — plain CSS classes in `front/src/app/globals.css` on theme vars; one-off layout and HeroUI slot overrides — Tailwind utilities inline; one pattern = one vocabulary. Details: `docs/WORKFLOW.md`.
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes` (normalizes to LF on add/checkout, overrides `core.autocrlf`) and prek hooks (`mixed-line-ending --fix=lf`, `fix-byte-order-marker`). Details: `docs/WORKFLOW.md`.
- **Commits:** conventional commits `type(scope): …` + body Why/What/Verified (template and branching rules — `docs/WORKFLOW.md`). The user commits themselves; don't commit without an explicit request.
- **Before delivery:** typecheck + lint + tests on both sides (see the table). Backend tests recreate a dedicated `rc_test*` PostgreSQL schema through Alembic. CI runs the full suite against both MemoryStore and real Valkey. Real-store local runs require a disposable Valkey DB 15 and explicit reset opt-in; see `docs/WORKFLOW.md`. Never point the reset-enabled fixture at shared services.
- **Sync frontend/backend changes into a running dev stack by restarting it with `just dev`** (compose `--watch` syncs `front/src`/`back/src`), **not by `docker cp` into `rc-front-dev`/`rc-back-dev`** — docker cp leaves the container out of sync with the repo and silently diverges from what a fresh checkout builds. If `just dev` is already running someplace, its watch picks the edits up on save; otherwise start it. Don't hand-copy files into containers to "test fast".
- **e2e:** don't run without the full stack (Next + back + Postgres + seed, `KEYCLOAK_ENABLED=false`); workers=1, chromium.
- **Caching reads (`back/src/common/read_cache.py`):** cache a table only when it is read on nearly every request AND has a cheap token of its own change (`count(*)` + `max(updated_at)`) — never a timer. Cached today: the renovation reference catalogue and КППК `read_excise_products` (both sit in per-request paths). **Deliberately not cached:** the «Сырьё» and КППК dictionaries — they are read once per page load, so a cache would buy nothing and only add a staleness risk, and the plan's row counts were tried and reverted (a correct token cost more than the count). The tests clear the cache per test (`conftest.py`), since every test recreates the schema.
- **Languages:** code comments/docstrings, commits and this documentation — English; user-facing strings (UI labels, toasts, API errors) — Russian. See the "Languages" section in `docs/WORKFLOW.md`.

## Documentation

- `docs/ARCHITECTURE.md` — system diagram, environment variables, data model, API, auth, deploy.
- `docs/COLLAB.md` — WebSocket protocol, leases, presence, undo/redo, locks.
- `docs/GRID.md` — revo-grid: forms and columns, clipboard, selection, coordinates, tooltips; the plan sections are split by topic (`###`), so jump to the heading.
- `docs/BACKEND_DEDUP_AUDIT.md` — backend duplication/seam audit, shared platform boundaries and follow-up recommendations.
- `docs/WORKFLOW.md` — environment, quality, languages standard, pre-commit (prek), branching/commits, working principles.
- `docs/KEYCLOAK.md` — corporate Keycloak: OIDC flow, prod config, claims→user, provisioning, local stand, diagnostics. Prod artifacts — `docs/keycloak/` (OIDC discovery, access token example).
- **Historical, not the current contract** (read only for background): `docs/RENOVATION_MIGRATION_CONTEXT.md`, `docs/KPPK_GAP_ANALYSIS.md`. Both open with a status note saying which passage is outdated.

**When behavior changes — update the corresponding file in `docs/`.** That's a requirement, not a wish: the docs are the only source of context for future agents.
