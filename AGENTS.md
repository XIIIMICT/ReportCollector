# AGENTS.md — ReportCollector

Cheat sheet for agents. Entry point: read only the section you need; details — in `docs/*`.

## What it is

Corporate raw-material planning system («Сырьё»): Excel-like **«Движение»** and **«Цены»** forms + dictionaries (Сырьевые позиции, Активы, Базисы поставки, Пользователи), real-time collaborative editing (cell leases, presence), report-month fixing with a version archive and Excel export.

- `front/` — Next.js 16 (App Router, React 19, revo-grid 4.27, HeroUI, Tailwind 4, Zod 4), package manager **bun**.
- `back/` — FastAPI + SQLModel + Alembic (Python 3.14, **uv**), asyncpg, Redis/Valkey for collaboration.
- Ports: front **5000**, back **8000** — from the root `.env` (`FRONT_PORT`/`BACKEND_PORT`), mandatory.
- Docker: `compose.yaml` (profiles `prod`/`dev`/`standalone`); in prod nginx runs **outside** Docker.

## Commands (from the repo root)

| What | Command |
|---|---|
| Back tests | `uv run --directory back pytest -q` |
| Front unit tests | `bun run --cwd front test` |
| e2e (needs a running stack) | `bun run --cwd front test:e2e` (`E2E_BASE_URL`, `KEYCLOAK_ENABLED=false`) |
| Back lint/format/type | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` |
| Front lint/format/type | `bun run --cwd front lint` · `format` · `typecheck` |
| Everything at once | `just lint` · `just typecheck` · `just test` · `just e2e` |
| Stack dev/prod | `just dev` (hot-reload, `--watch`) · `just prod` |
| DB / keycloak | `just db` · `just keycloak` (standalone profile) |
| Migrations | `just migrate` (never edit already-applied prod migrations — only new ones). The migration connection sets `lock_timeout=30s` — a blocked migration (e.g. an ALTER waiting behind the running app) fails loudly instead of hanging |
| Seed | `just seed` — **fully recreates the DB schema**, don't run without asking |

## Landmines (critical!)

1. **`AUTH_APP_ORIGIN` — no trailing slash.** compose derives the CORS origins from it; the browser never sends a slash in `Origin` → the WS handshake fails with 403. `back/src/config.py` normalizes slashes, but the correct `.env` has none.
2. **Programmatic selection in revo-grid — only `grid.setCellsFocus(start,end)` + `grid.refresh()`.** `SelectionStore.setRange` is a dead API (in `revogr-overlay-selection` there's a raw observable, no such method): the call silently crashes the handler. Reset — `grid.clearFocus()` **also needs `grid.refresh()`**, and **`grid.pendingColumnFocusRestore = undefined`**: after any `columns` change while a range was active, `columnChanged` caches the focus and `restoreColumnFocusAfterRender` re-applies a single-cell range on the next render — its "column didn't move" guard reads the store, which `clearFocus()` just emptied, so it can't match. Row selection must clear the range **before** `setSelectedRowIds` (selectedRowIds is a `columns` dependency → the rebuild captures the live focus), or the border resurrects after the click.
3. **Sorting/filtering: screen index ≠ `rows` array index.** Any "index → row" mapping (paste, cut, copy, presence, `canPasteRange`, row shift-select, Ctrl+A) must go through `getVisibleRows()` = `grid.getVisibleSource("rgRow")`. Otherwise values are committed to the wrong row → fake asset "deletions" → FK RESTRICT.
4. **Excel paste:** trim trailing empty rows/columns from the buffer (Excel puts a trailing `\n`) — otherwise an extra empty cell below overwrites a neighbor.
5. **Hotkeys — by `event.code` (`KeyC`/`KeyV`/`KeyX`/`KeyA`/`KeyZ`/`KeyY`/`Delete`), not `event.key`** — otherwise broken with the Russian layout. Ctrl+Delete opens the delete dialog (all dictionary grids + the renovation plan). On the «Ремонт» plan **Ctrl+A is fully disabled** — the form holds ~94k rows, a whole-form copy would overflow the clipboard; the key is swallowed, revo-grid's built-in select-all included.
6. **`bun run --cwd front format` (oxfmt) rewrites line breaks in untouched files** (`use-collab-room.ts`, `ws-client.ts`). After format check `git status`/`git diff` and restore foreign files: `git checkout -- <file>`.
7. **Emails — lowercase only and through validators:** back — `EmailStr`/`email_validator`, front — `z.email()`. Both **reject special-use domains** (`.local`, `.localhost`, `example.com`) — use `@example.ru` in tests. Case-insensitivity: lookup by `lower(email)`, Keycloak finds the existing user.
8. **The business plan is edited only in «Цены»** (`businessPlanQuantityTons` etc.); in «Движение» the `businessPlanTons` column is a projection, non-editable. Don't "project" edits between forms.
9. **Fixing («Зафиксировать»)** — only the first open month, only with complete rows and no active leases; the preliminary version is overwritten, the final one is immutable; a final fix opens the next month and **clears all undo/redo history**.
10. **Undo/redo is scoped per session and form** (`rc:undo:{user}:{session}:{scope}`): «Цены» history doesn't undo «Движение». The `session_id` is a page-lifetime id the frontend sends with every edit/undo call (`getCollabSessionId()` in `ws-client.ts`) — a page reload starts with empty history. Don't drop it from PATCH/undo/redo calls (backend requires it).
11. **Presence vs lease:** lease (TTL 20s, renew 10s) — the user is editing; presence (TTL 25s, heartbeat 15s) — the user is "online", including a completed selection. Idle >2 min or hidden tab → leases released, heartbeat stops; on return — re-join via ping.
12. **Archive «Движение» version — read-only**: no editing and no presence.
13. **«Пользователи» dictionary:** an admin adds a person — they appear in all projects (in others `is_active=false`); the first successful Keycloak login fills in the data (FIO overwritten from Keycloak) and activates where `is_active=true`.

## Working rules

- **Git:** root `.gitignore` — deny-all + explicit whitelist (`back/`, `front/`, `docs/`, `infra/`, `.github/` etc. restored as whitelists in their own files). A new root file must be explicitly whitelisted. Nested `back/.gitignore`, `front/.gitignore` are whitelist-style too.
- **Don't commit debug files:** `back/tests/debug_*.py`, `front/e2e/*-probe*`, `front/e2e/probe/` (the root ignore already silences `*.mjs`/`*.cjs`). Delete them after debugging.
- **A11y label rule:** a form control without a visible `Label` must have an `aria-label` — react-aria otherwise warns on every mount and floods dev logs (`docker logs`). Details: `docs/WORKFLOW.md`.
- **Front styling:** semantic, recurring UI — plain CSS classes in `front/src/app/globals.css` on theme vars; one-off layout and HeroUI slot overrides — Tailwind utilities inline; one pattern = one vocabulary. Details: `docs/WORKFLOW.md`.
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes` (normalizes to LF on add/checkout, overrides `core.autocrlf`) and prek hooks (`mixed-line-ending --fix=lf`, `fix-byte-order-marker`). Details: `docs/WORKFLOW.md`.
- **Commits:** conventional commits `type(scope): …` + body Why/What/Verified (template and branching rules — `docs/WORKFLOW.md`). The user commits themselves; don't commit without an explicit request.
- **Before delivery:** typecheck + lint + tests on both sides (see the table). Back tests require a running Postgres with `back/tests/conftest.py` defaults (`postgres/strong_password@localhost:5432/dwh`, schema `rc_test` recreated per test, collab on an in-memory store).
- **Sync frontend/backend changes into a running dev stack by restarting it with `just dev`** (compose `--watch` syncs `front/src`/`back/src`), **not by `docker cp` into `rc-front-dev`/`rc-back-dev`** — docker cp leaves the container out of sync with the repo and silently diverges from what a fresh checkout builds. If `just dev` is already running someplace, its watch picks the edits up on save; otherwise start it. Don't hand-copy files into containers to "test fast".
- **e2e:** don't run without the full stack (Next + back + Postgres + seed, `KEYCLOAK_ENABLED=false`); workers=1, chromium.
- **Languages:** code comments/docstrings, commits and this documentation — English; user-facing strings (UI labels, toasts, API errors) — Russian. See the "Languages" section in `docs/WORKFLOW.md`.

## Documentation

- `docs/ARCHITECTURE.md` — system diagram, environment variables, data model, API, auth, deploy.
- `docs/COLLAB.md` — WebSocket protocol, leases, presence, undo/redo, locks.
- `docs/GRID.md` — revo-grid: forms and columns, clipboard, selection, coordinates, tooltips.
- `docs/WORKFLOW.md` — environment, quality, languages standard, pre-commit (prek), branching/commits, working principles.
- `docs/KEYCLOAK.md` — corporate Keycloak: OIDC flow, prod config, claims→user, provisioning, local stand, diagnostics. Prod artifacts — `docs/keycloak/` (OIDC discovery, access token example).

**When behavior changes — update the corresponding file in `docs/`.** That's a requirement, not a wish: the docs are the only source of context for future agents.
