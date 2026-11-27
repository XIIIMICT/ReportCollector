# AGENTS.md — ReportCollector

Thin index. Details live in `docs/*`; area rules live in `back/AGENTS.md` and
`front/AGENTS.md` (loaded on demand when you work in those directories). Read
only the section your task needs, then follow the pointer.

## What it is

Corporate raw-material planning system: Excel-like «Движение» and «Цены» forms,
dictionaries, real-time collaborative editing (cell leases, presence),
report-month fixing with a version archive, and Excel import/export. Two further
projects: **КППК** (`/kppk`, corporate `raw.kppk_*` dumps loaded by the operator)
and **Логистика** (`/logistics`, role-based rates on the external `dict` schema).
Domain contracts — `docs/DOMAINS.md`; collaboration protocol — `docs/COLLAB.md`.

- `front/` — Next.js 16.4.6 (App Router, React 19, revo-grid 4.32.0, HeroUI v3,
  Tailwind 4, Zod 4), Bun 1.4.x. Frontend rules — `front/AGENTS.md`.
- `back/` — FastAPI + SQLModel + Alembic (Python 3.15, **uv**), asyncpg, Valkey.
  Backend and cross-stack data rules — `back/AGENTS.md`.
- Ports: `FRONT_PORT=5000` / `BACKEND_PORT=8000` from the root `.env` (mandatory) are **container-side only** — the app publishes no host ports. Only nginx is public (`80` → `443` TLS); DB/monitoring/Keycloak admin ports bind `127.0.0.1`; `infra/check_host_binds.py` guards the rendered config in CI. Full per-port decision table — `docs/ARCHITECTURE.md`, «Published-port contract».
- Compose: `compose.yaml` with `prod`/`dev` profiles; infrastructure is started
  explicitly — `just db-stand`, `just auth-stand`, `just monitoring-stand`,
  `just proxy-stand`. **Start `just proxy-stand` before the app**: without it the browser has no entry point (for dev set `NGINX_UPSTREAM_HOST=front-dev` / `NGINX_BACKEND_HOST=back-dev`). In prod the reverse proxy lives in the central DWH stack; TLS and `/api/ws/` are already configured there.

## Commands (from the repo root)

| What | Command |
|---|---|
| Back tests | `uv run --directory back pytest -q` |
| Front unit tests | `bun run --cwd front test` |
| e2e smoke (running stack required) | `bun run --cwd front test:e2e --project=smoke` (`E2E_BASE_URL`, `KEYCLOAK_ENABLED=false`); local default origin is the proxy `https://127.0.0.1`, CI uses the isolated stand `http://127.0.0.1:15000`; `--project=ui-keycloak` needs the separately prepared stack |
| Back lint/format/type | `uv run --directory back ruff check --fix` · `ruff format` · `ty check --fix` |
| Front lint/format/type | `bun run --cwd front lint` · `format` · `typecheck` |
| Everything at once | `just lint` · `just typecheck` · `just test` · `just e2e` |
| Stack dev/prod | `just dev` (hot reload, `--watch`) · `just prod` |
| Infrastructure | `just db-stand` (postgres + valkey) · `just auth-stand` (keycloak) |
| Observability | `just monitoring-stand` (full stack) · `just monitoring` (Alloy only, external Loki) · `just drift-check` (shared-config drift). Backend `/metrics`; logs carry `env=<LOG_ENV>`. Mail routing and the SMTP-relay deviation — `docs/ARCHITECTURE.md` |
| PostgreSQL backup | `just backup-stand` (daily dump + weekly restore drill) · `just backup-now` · `just backup-drill` · `just backup-logs`. Contract, restore procedure and alert response — `docs/RUNBOOKS.md` |
| Standalone proxy | `just proxy-stand` (80/443 → front + WS back; same files as the central stack) |
| Migrations | `just migrate` — never edit an applied prod migration. The connection sets `lock_timeout=30s`: a blocked migration fails loudly |
| New DB / legacy adoption | New: `just migrate`. Adopting legacy head `0015_plan_row_col_218` is unsupported after 0041 (`r_plan_rows` was rebuilt) — `docs/WORKFLOW.md` |
| Seeds | `just seed` **recreates the schema — ask first**; `just seed-kppk`, `just seed-logistics`, `just seed-dev-dict` (local/CI only, never prod). Details — `back/AGENTS.md` |

## Tooling

Use the commands exactly as written. **If a required tool is missing, stop and ask
for installation; never substitute an equivalent.** `python -m pytest` silently
picks another interpreter (use `uv run pytest`); `npx` picks another resolver.

- **Required:** `uv`, `bun`, `docker`, `git`, `just`.
- Prefer `rg`; built-in `grep`/`glob`/`read` avoid shell quoting traps. `jq`/`yq` are optional. Use `gh` to inspect GitHub Actions runs and failed logs (`gh run list`, `gh run view --log-failed`) instead of guessing CI state from local results.
- If any of `uv`, `bun`, `rg` or `gh` is not installed, stop and ask the maintainer to install it; do not silently substitute a different tool or skip the check.
- `.mcp.json` declares `heroui-react` (v3 docs), `next-devtools` (installed Next
  version) and `browsermcp` (the only supported way to drive the user's Chrome).
  All are launched with `bunx`. If a server is unavailable, say so and ask.
- HeroUI reference: `https://heroui.com/react/llms.txt` (index), `llms-full.txt`
  (5.8 MB — fetch one page or `rg` a local copy, never load whole), `DESIGN.md`.
  v2 docs do not apply. See `front/AGENTS.md`.
- Live-stack login: `evtushenko` / `evtushenko` (all projects). `admin/admin` is
  the Keycloak console, not an app account. Details — `docs/KEYCLOAK.md`.
- No throwaway helper scripts when one command or built-in tool suffices; an
  untracked helper becomes the next agent's cleanup job.
- **Cyrillic does not survive the PowerShell pipeline.** Edit Russian files with
  `read`/`edit`/`write`, never shell text replacement; re-read seemingly garbled
  shell output before "fixing" anything (e.g. a fresh `psql` call writing to a
  file); compare Russian labels with
  `SELECT count(*) … WHERE name = :v`, not by eye.

## Working rules

- **One logical task per branch.** Create `feat/<short>` / `fix/<short>` /
  `docs/<short>` from current `dev` before editing; merge into `dev` with
  `git merge --no-ff` before starting the next task; squash only `dev → main`.
  One logical change per commit, body Why/What/Verified — `docs/WORKFLOW.md`.
- **When in doubt, ask.** Ambiguous product/config/process decisions → stop;
  safe reversible choices may proceed with documentation. Missing tool → ask;
  red checks → don't report done; foreign/untracked work → never discard.
- **Definition of done:** quality checks + CI green, behaviour verified by hand
  where tests can't see it, docs updated in the same change, no debug artifacts
  or secrets, every ambiguity asked — `docs/WORKFLOW.md`.
- **One home per fact:** keep a line plus a pointer here, never a full copy;
  record new landmines in the matching `docs/` section or nested guide.
- **Git:** root `.gitignore` is deny-all + explicit whitelists (`back/`, `front/`,
  `docs/`, `infra/`, `.github/`); a new root file must be whitelisted. Check
  `git status` and branch before any git operation. Never commit debug files
  (`back/tests/debug_*.py`, `front/e2e/*-probe*`, `front/e2e/probe/`).
- **Files:** UTF-8, no BOM, LF (`.gitattributes` + prek).
- **Commits:** conventional `type(scope): …`, body Why/What/Verified.
- **Hook policy (maintainer decision 2026-11-25):** every static checker (ruff,
  ty, oxfmt/oxlint/stylelint, `bun check`) runs on **staged files** at commit and
  the **whole tree** in CI; no `manual` stage in any `prek.toml`.
- **Docstring policy:** D100–D107 are intentionally ignored in
  `back/pyproject.toml`. Write a docstring only for a non-obvious contract or
  the reason behind a decision; do not add trivial ones just to satisfy a linter.
- **Before delivery:** typecheck + lint + tests on both sides. Backend tests
  recreate a dedicated `rc_test*` schema; the Valkey fixture uses disposable DB
  15 with explicit reset opt-in — never point it at shared services.
- **Sync a running dev stack with `just dev`** (compose `--watch`), not
  `docker cp` into `rc-*-dev` — copies silently diverge from a fresh build.
- **e2e:** never without the full stack (Next + back + Postgres + seed,
  `KEYCLOAK_ENABLED=false`); workers=1, chromium.
- **Languages:** code comments/docstrings, commits and docs — English;
  user-facing strings (UI, toasts, API errors) — Russian. `docs/WORKFLOW.md`.

## Documentation

- `docs/ARCHITECTURE.md` — diagram, env vars, data model, API, auth, deploy,
  monitoring and implemented invariants (canonical SMTP/mail section).
- `docs/DATA-JOBS.md` — renovation import/preflight, durable jobs, streaming XLSX.
- `docs/FRONTEND-REFACTOR.md` — frontend baseline measurements, deduplication progress, Zustand decision.
- `docs/DOMAINS.md` — КППК and Логистика data models.
- `docs/RENOVATION-DESIGN.md` — service/router/API boundaries and query cost.
- `docs/COLLAB.md` — WebSocket protocol, leases, presence, undo/redo, locks.
- `docs/GRID.md` — revo-grid forms, columns, clipboard, selection, coordinates,
  tooltips; split into `###` sections — jump to the heading.
- `docs/TODO.md` — the single engineering backlog and product deferrals.
- `docs/LOAD-PROFILES.md` — ASSUMPTION-only target load profiles (no usage data).
- `docs/LOAD-BASELINE.md` — measured SCALED-DOWN k6 baseline, exact commands and caveats; suite in `load/`, on-demand workflow `.github/workflows/load-baseline.yaml`.
- `docs/PRESENCE-FANOUT.md` — P1-3 coalesced-presence before/after k6 measurements (N=50/100/200) and UNMEASURED caveats.
- `docs/LEASE-BATCHING.md` — verified lease/token model, batched EVALSHA semantics and before/after k6 measurements.
- `docs/RUNBOOKS.md` — PostgreSQL backup/restore runbook: schedule, retention,
  RPO assumption, drill cadence, off-host config, monitoring contract and alert response.
- `docs/LIMITS.md` — owner-approved Phase 9 limit decisions.
- `docs/WORKFLOW.md` — canonical process: quality, languages, prek, branches.
- `docs/KEYCLOAK.md` — OIDC flow, prod config, claims→user, diagnostics. Prod
  artifacts (OIDC discovery, access-token example) — `docs/keycloak/`.
- `docs/samples/renovation_td_example.xlsb` — the committed importer template.
- Historical only: `docs/RENOVATION_MIGRATION_CONTEXT.md`.

**When behavior changes — update the corresponding file in `docs/`.** That's a
requirement, not a wish: the docs are the source of context for future agents.
