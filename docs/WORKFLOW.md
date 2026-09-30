# Workflow (environment, quality, tests)

## Environment

Use Bun 1.4.x (CI and Docker use the 1.4 release line), Python 3.14 and Node 26 for the production frontend runtime.

1. Copy the config: `cp .env.example .env` (root — single source). To run the front on the host additionally `cp .env.example front/.env.local`.
2. Dependencies: `just deps` (`uv sync --directory back` + `bun install --cwd front`).
3. DB and services: `just db` (postgres+valkey), Keycloak if needed — `just keycloak`.
4. Migrations: `just migrate`. The migration connection runs with `lock_timeout=30s` (`back/migrations/env.py`) — if the app holds a lock (classic idle-in-transaction pool connection), the migration aborts with "canceling statement due to lock timeout" after 30s instead of hanging forever. On prod prefer running migrations with the app stopped/idle. Seed: `just seed` (**recreates the schema!** only with consent, never on prod).
   - The renovation plan comes from the per-enterprise «РБ ПОФ_2027_*» workbooks in `docs/seed/Планы/` (each ships one «РБ…» sheet; header on row 1, data on row 3). `just seed-renovation-plan` (`uv run --directory back -m src.scripts.seed_database --renovation-plan`) rebuilds **only** the plan (truncates `renovation_plans`/`renovation_plan_rows`) while keeping the asset dictionary, the reference catalogs and the users — the importer resolves the enterprise by its «Наименование ДО» code against the existing `r_assets` ids. `just seed-renovation` (`--renovation`) additionally rebuilds the catalogs. Columns are matched by header text; a small **closed alias list** maps the workbooks' differing names to the form fields (e.g. «Лот» → the direction code `col_189`, the third segment of the work id, which the «РБ ПОФ» files name «Лот» rather than the form's «Код направления деятельности»). Values not in a catalog are added as marked options (label prefixed «@») that an admin can review and delete afterwards. Run seeds on the host with `POSTGRES_HOST=127.0.0.1` (the `.env` value `host.docker.internal` only resolves inside containers).
5. Run: `just dev` (compose + hot-reload) or on the host `just back` / `just front`.

About `POSTGRES_HOST`: on the server it's the real DB address (not localhost); `just migrate` uses whatever is in `.env`. In the compose network — `host.docker.internal` or the service name.

**Valkey in the dev stack:** `compose.yaml` overrides `VALKEY_URL=redis://valkey:6379/0` for `back-dev` (the compose service on the same network). Don't point the dev backend at `host.docker.internal` — Docker Desktop's VM loopback times out under load (`redis.exceptions.TimeoutError`) and breaks collab presence/locks. The prod profile keeps `VALKEY_URL` from `.env` (valkey may live on the host there).

**Postgres in the dev stack:** `compose.yaml` likewise pins `POSTGRES_HOST=postgres` for `back-dev`. The `rc-postgres` container is on both `rc-net` and `rc-net-dev`, so the service name resolves inside the dev network, like `valkey`. The `.env` value stays `localhost`/`host.docker.internal` for **host** runs (`just back`/`just front`, seeds, tests); inside a container `localhost` is the container itself — the symptom is `/health` 200 but every DB-backed request (e.g. `GET /api/me`) returns **500** with `ConnectionRefusedError: [Errno 111]`. After changing this, recreate the container (`docker compose --profile dev up -d back-dev`).

## Quality (what we check before delivery)

| Side | Lint | Format | Typecheck | Tests |
|---|---|---|---|---|
| back | `uv run --directory back ruff check --fix` | `uv run --directory back ruff format` | `uv run --directory back ty check` | `uv run --directory back pytest -q` |
| front | `bun run --cwd front lint` (oxlint --fix) | `bun run --cwd front format` (oxfmt) | `bun run --cwd front typecheck` (tsc --noEmit) | `bun run --cwd front test` (bun test, happy-dom) |
| e2e | — | — | — | `bun run --cwd front test:e2e` (Playwright, chromium, workers=1) |

Aggregators: `just lint`, `just typecheck`, `just test`, `just e2e`.

### Back

- `ruff` with rule `ALL` and explicit exclusions (see `back/pyproject.toml`; Cyrillic `RUF001-003` allowed, docstrings optional, camelCase schema fields `N815` allowed).
- `ty check` — static type checker (not mypy!). Type errors are a red flag.
- `pytest`: `asyncio_mode=auto`, `filterwarnings=error`. **Postgres required**: defaults are `postgres/strong_password@localhost:5432/dwh`; a dedicated `rc_test*` schema is recreated through `alembic upgrade head` per test, not `metadata.create_all`. Override `TEST_DATABASE_URL`/`TEST_SCHEMA` for an isolated instance. Production schemas are rejected by the fixture.
- `TEST_COLLAB_BACKEND=memory` is the local default. For the complete suite on real Valkey, set `TEST_COLLAB_BACKEND=valkey`, `TEST_VALKEY_URL=redis://127.0.0.1:16379/15`, and `TEST_VALKEY_RESET=1`. **DB 15 is flushed before/after each test**: use only the disposable CI Valkey instance, never a shared/dev/production server. The explicit opt-in and `/15` guard prevent accidental use of the application's DB 0. Do not run both store modes concurrently against the same test schema or Valkey database.
- Test files: `back/tests/` (`test_collab.py`, `test_raw_materials.py`, `test_approval.py`, `test_local_login.py`, `test_auth_scenario.py`, `test_export.py`, `test_config.py`). Fixtures `client` (HTTP, ASGI), `db_session` (clean seed), `signed_identity_headers` — in conftest.
- Test emails — `@example.ru` (validators reject `.local`/`example.com`).

### Front

- `oxlint --fix`, `oxfmt` (oxc formatter), `tsc --noEmit`.
- **Attention:** `bun format` (oxfmt) may rewrite line breaks in untouched files (regularly touches `src/shared/collab/use-collab-room.ts`, `ws-client.ts`). After format: `git status`/`git diff` — if the diff on foreign files is empty, restore them with `git checkout -- <file>`.
- **A11y label rule (dev-log noise):** every HeroUI/react-aria form control **without a visible `Label` must have an `aria-label`** — `SearchField`, `Autocomplete`, the `SearchAutocomplete` pre-mount `TextField` fallback, etc. Missing it makes react-aria warn on every mount ("If you do not provide a visible label…"). The warning exists only in dev builds (`NODE_ENV !== 'production'`), but it floods `docker logs` and drowns real errors. The label is user-facing → Russian, short (`aria-label="Найти актив"`). Don't chase other a11y rules — only this label rule; visible `Label`s (e.g. in `profile-modal`) and buttons with text are already fine.
- Unit tests — bun test (happy-dom), in `__tests__` next to the code.

### Front styling (Tailwind vs plain CSS)

Tailwind 4 is a hard dependency of HeroUI — `@heroui/styles` is built on it (`@tailwindcss/postcss` in `postcss.config.ts`), and utilities like `text-muted`, `bg-background`, `text-danger` come from HeroUI's theme layer mapped to the CSS variables defined in `front/src/app/globals.css`. The question is not "Tailwind or not" (it can't be removed without dropping HeroUI) but where the boundary sits:

- **Semantic, recurring UI** (grid, table footer, collab bar, toolbar, autocomplete, action bar) — plain CSS classes in `front/src/app/globals.css` (BEM-ish names), themed via the variables (`var(--text)`, `var(--muted)`, …) with media queries and state selectors. This is the project's main vocabulary.
- **One-off layout** (loading/error states, the blocked page) and **HeroUI slot overrides** (`Card.Content className="p-8"`, `size-4` icons, `Skeleton` shapes) — Tailwind utilities inline. Don't invent a BEM class for a single use.
- **One pattern = one vocabulary:** don't express the same visual in both ways in one component — e.g. muted text is either the `text-muted` utility on a HeroUI slot or `color: var(--muted)` in a class, not both in the same tree.
- A utility that spreads to several places graduates into a class in globals.css (rename the usages in the same change).

### e2e (Playwright)

Select the authentication environment explicitly: `bun run --cwd front test:e2e --project=smoke` uses local login; `--project=keycloak` runs the existing Keycloak scenarios against a seeded local Keycloak stack on ports 5000/8080. The smoke helper honors `E2E_BASE_URL`. Missing local login is a failure, never a silent skip. Probe files are excluded from discovery and Git.

Both projects require a running stack and seed. `just e2e` selects smoke; its default origin is `http://127.0.0.1:5000`. The smoke project also includes clipboard and collaboration regression tests: login/logout, rejected passwords, anonymous redirects, both workspaces, persisted price/renovation edits, disabled Ctrl+A on renovation, undo/redo, sorted Excel paste, Russian-layout copy, reference validation, two-user lease denial/update propagation, asset-scoped presence and multi-tab selection. Do not run either project against an unprepared stack.

### GitHub Actions

`.github/workflows/ci.yaml` is the single CI entry point (PRs to main/dev, pushes to main/dev, manual runs and reusable release checks). Configure **CI required** as the required branch-protection check. There are no workflow path filters that could leave that check pending.

- Backend: locked dependencies, Ruff lint/format, ty and the **entire pytest suite in a memory/valkey matrix**, with PostgreSQL 18 and Valkey 8. Each matrix job owns its disposable services. XML/JUnit and raw coverage data are uploaded separately. A required coverage job merges both runs and enforces 80% combined statement/branch coverage; neither individual transport run exercises the other implementation. Dedicated cross-connection tests additionally exercise server TTL, pub/sub and lease ownership. Missing Valkey is a CI failure, not a fallback to MemoryStore.
- Frontend: Bun 1.4, generated Next route types, TypeScript, oxlint, oxfmt and Bun tests with LCOV. Next type generation is required before typecheck in a fresh checkout.
- Configuration: actionlint with ShellCheck plus semantic Compose validation (including custom `!override` tags).
- Production: build each image once, load it, migrate PostgreSQL from the host with uv, insert the small seed using `--data-only` (no schema creation), start the same images with two backend workers and run Chromium regressions. The image contains no local Excel workbook. `infra/compose.ci.yaml` never loads the developer's `.env` or log directories; localhost ports are 15000/18000 for front/back and 15432/16379 for PostgreSQL/Valkey. Browsers connect directly to the backend WS port.
- All jobs have timeouts; reports and container logs are retained for seven days. The stack is removed even on failure.
- Release tags `vMAJOR.MINOR.PATCH` invoke the same CI on the tagged commit, then publish the tested image archive to GHCR (version and full commit SHA tags). Images are not rebuilt after tests. No automatic server deployment or mutable `latest` tag is performed.

Local reproduction: build with `docker compose -f infra/compose.ci.yaml build`, start `postgres valkey` with `up -d --wait`. For the host migration set `DATABASE_URL=postgresql+asyncpg://postgres:ci_password@127.0.0.1:15432/rc_ci`, `POSTGRES_SCHEMA=rc`, `AUTH_SECRET=ci-only-secret-at-least-32-characters-long`, then run `uv run --directory back python -m alembic upgrade head`. Insert data with `docker compose -f infra/compose.ci.yaml run --rm --no-deps back python -m src.scripts.seed_database --data-only` and start `up -d --no-build --wait back front`. Set `E2E_BASE_URL=http://127.0.0.1:15000` and run the smoke project. Cleanup only this isolated project with `docker compose -f infra/compose.ci.yaml down --volumes`.

Keycloak-specific profile/hover suites and large-workbook performance checks remain explicit local checks; they are not represented as passing PR checks. Corporate SSO, external nginx/TLS and deployment rollback require a separate staging environment. Unit/component tests cover API errors/contracts, signed sessions, WebSocket lifecycle/protocol, grid edit mapping, export and real HeroUI tab selection; they do not imply exhaustive UI coverage.

### CodeQL security analysis

`.github/workflows/codeql.yaml` is a separate advanced-setup security workflow for JavaScript/TypeScript, Python and GitHub Actions. It runs on pushes and PRs to `main`/`dev`, manual dispatch, and weekly on the default branch. Scheduled runs and the manual-dispatch UI require the workflow on the default branch. There are no path filters, so Python-only changes and dependency changes are analyzed too.

All three languages use `build-mode: none`: no Bun/uv dependency installation, Next build, database, Valkey, Keycloak or production secrets are needed. GitHub-hosted Ubuntu 24.04 supports CodeQL Action v4. Each language has its own analysis category and a 20-minute timeout; newer runs cancel obsolete runs on the same ref.

Code scanning must be enabled in repository settings; private repositories require GitHub Code Security availability. Use advanced setup for this workflow, not a simultaneous default-setup configuration. Findings are uploaded to Security / Code scanning. These checks are separate from `CI required`: configure a code-scanning ruleset or the language checks in branch protection if they must block merges. A successful analysis means the scan completed, not that no alerts exist. GitHub permissions, licensing and actual extraction results must be verified in the first hosted run.

### New database versus existing database

The 2026-09-07 consolidation replaces the fifteen historical revisions with the frozen `0001_baseline`. This was explicitly authorized for installations already at the old head. Do not edit this applied baseline again: future changes require new revisions.

- **New empty database:** `uv run --directory back python -m alembic upgrade head` creates the schema and all tables transactionally. No bootstrap command is needed.
- **Existing database at `0015_plan_row_col_218`:** take and verify a backup, stop application writes and pin this transition release. Run `just migrate` (equivalent to `uv run --directory back python -m migrations.upgrade`). The wrapper locks the version table, validates the legacy schema through `migrations.adopt_baseline`, stamps `0001_baseline`, and upgrades to head in the same transaction. A failure rolls back both the marker and DDL. Unknown/older revisions and schema drift fail closed; investigate rather than stamping blindly. Business data and legacy `rm_cell_state`/`rm_edit_log` tables are preserved. Use this pinned transition release: its baseline comparison excludes only the new collaboration tables. Direct `alembic upgrade head` cannot resolve the removed legacy revision; use the wrapper for this transition.
- **The wrapper prunes the retired chain's revision files first.** A checkout updated in place (a plain `git pull`, which never deletes files) keeps the fifteen deleted revisions as untracked files; because the new chain reuses some numbers, Alembic would see several heads and fail with «Multiple head revisions are present for given argument 'head'». `migrations.upgrade`/`adopt_baseline` therefore delete exactly the known historical filenames (and their stale bytecode) before reading `migrations/versions`. An unexpected extra revision is left in place, so a genuine conflict still surfaces.
- **Already adopted database:** use `just migrate` normally. Never run baseline downgrade on a populated database: it drops application tables. Restore the backup and previous release for rollback of the transition.

Migrations and Alembic configuration are **not copied into runtime Docker images**. Run them on the host/CI with uv and the same release checkout before starting application containers. Tests cover an empty upgrade/downgrade roundtrip, all-column business-data snapshots before/after adoption, rollback of completed DDL/sequences/defaults/revision markers, and refusal of revision/schema/enum drift. These fixtures reproduce the known legacy schema variants; they are not a restored production backup. Production adoption itself remains an operator action; CI does not connect to production.

### Transactional journal upgrade

After baseline adoption, run `alembic upgrade head` from this checkout to apply `0002_transactional_journal`. Stop all old application workers first: this is not a mixed-version rolling upgrade. Back up PostgreSQL before the maintenance window, apply migrations on the host, start the new images, and verify `/ready` and a save/undo/reconnect smoke test. Do not run `seed --reset` on an existing installation.

Business rows are preserved. Existing temporary Valkey undo stacks are not imported; users start fresh history. New history lives in PostgreSQL, is capped at 500 actions per session/form and expires after 24 hours of inactivity. Downgrading the new migration removes its history/revision/outbox tables (including pending notifications), not business data; do not downgrade while workers are running.

`0003_reference_created_default` preserves the `DEFAULT now()` on `renovation_reference_values.created_at` originally introduced by legacy `0008`. The old `create_all` path and frozen baseline omitted this default. Adoption accepts these two known variants only; arbitrary timestamp defaults still fail validation. `just migrate` converges both variants to `now()` without changing existing timestamps. Already-adopted installations also receive this correction through the new revision; applied baseline/journal migrations are unchanged. Downgrading 0003 removes only the default, restoring the frozen 0002 schema; it does not change timestamp values. An upgrade failure rolls back both adoption and this DDL.

`0004_block_codes` renames the renovation responsibility-block labels of the native `r_block` enum from the owners' surnames (`tashpaev`/`borodiichuk`/`babaeva`) to the directorates (`tech`/`ksir`/`mto`). It creates a new enum type, remaps every `r_user_roles.block` value with `CASE` (`… ELSE NULL`), swaps the column type and drops the old type — old values are translated, never dropped, so no business row is lost (verified: legacy rows with the surname labels migrate to the matching directorate codes and the enum ends with exactly `{tech,ksir,mto}`). The `ALTER COLUMN … TYPE` takes a brief `ACCESS EXCLUSIVE` lock on `r_user_roles`; run it in the maintenance window with workers stopped, as with the other revisions, so the `lock_timeout=30s` guard does not fire. `migrations.adopt_baseline` recognises the pre-rename `r_block` labels as the known in-flight state, so an as-yet-unadopted `0015` installation still adopts and then converges through `0004` in the same `just migrate` run; any other `r_block` label difference is still rejected as schema drift. The downgrade maps the codes back to the surnames.

**Adoption tolerates five legacy-only variants** (found when adopting a real `0015` installation): indexes that SQLModel named after the model class (`ix_app_user_email`, `ix_rm_asset_raw_material_…`, `ix_rm_report_snapshot_…`) instead of the current table-based names (`ix_app_users_email`, `ix_rm_asset_raw_materials_…`, …); a column whose uniqueness is a `UNIQUE CONSTRAINT` plus a plain index instead of one unique index (`r_assets.code`); the `renovation_reference_values.metadata` column the models dropped (nullable, unused); indexes the model declares that a pre-adoption legacy schema does not carry yet because they are created by migrations running AFTER the baseline (`ix_user_roles_project`, `ix_r_user_roles_project` from `0005_user_role_project_indexes`); and the two `rm_raw_material_prices` audit timestamps (`business_plan_updated_at`, `storage_standard_updated_at`) still stored as `TEXT` before `0006_price_audit_timestamps` rewrites them. A `remove_index`/`add_index` pair covering the same table and columns is treated as equivalent (name and uniqueness-form differences only), the matching dropped constraint is accepted too, the extra column is preserved (not dropped), a missing post-baseline index is allowed through the closed `_LEGACY_MISSING_INDEXES` list, and the audit-timestamp type/nullability/default changes are allowed only for those two columns and only when the target is the expected `timestamptz` / nullable / no-default shape (`_LEGACY_PRICE_TIMESTAMPS`). Anything else — an unpaired index, a different column set, an unknown extra column, an index not on that list, a type change on any other column — still fails closed. The refusal message lists the object names (`remove_index rm_x(col) [ix_…]`, `remove_constraint r_assets(code)`), not a raw dump.

Local login semantics and corporate proxy download exceptions are intentionally unchanged. Capacity/load tests are out of scope for this change; the target of 50 concurrent users is not a measured service-level guarantee.

### Price audit timestamps and PostgreSQL 14

`0006_price_audit_timestamps` turns `rm_raw_material_prices.business_plan_updated_at` / `storage_standard_updated_at` from `TEXT` into `timestamptz`. Production still runs PostgreSQL 14, so the migration cannot use `pg_input_is_valid` (PostgreSQL 16+): it creates a throwaway plpgsql wrapper (`rc_migration_0006_safe_timestamptz`) that folds an unparseable legacy value to `NULL` and catches only `data_exception` (never a timeout/cancel), then drops it. A new migration that needs a safe cast must stay within the PostgreSQL 14 function set.

### Renovation computed fields (backfill)

The renovation importer skips every calculated field (`_SKIP_FIELDS`) and `read_plan_rows` returns stored values without materializing, so imported/adopted rows have blank formula columns until each row is edited. `recompute_plan_rows` (service) fills them; it is exposed as `just recompute-renovation-plan` (`uv run --directory back -m src.scripts.recompute_renovation_plan`, optional `--plan-id`). It runs once after importing a plan and whenever the formula definitions change; on PostgreSQL 14 it needs no schema change (data only). `just seed-renovation-plan` (and the plan part of `just seed`/`just seed-renovation`) calls it at the end of the import, so a fresh seed is already consistent. It streams rows by primary key and writes one batched `UPDATE` per 1000 rows (~4 min for 306k rows; a per-row ORM flush is ~12 min), leaves `revision`/`updated_at` untouched (it is a data fix, not an edit), and does not broadcast — reload clients after running it on a live plan.

### Renovation plan import is batched (do not materialize the whole plan)

The plan import is a generator (`iter_renovation_plan_rows`) inserted in batches of 5000 with `insert_renovation_plan_rows` (each batch flushed and expunged). It used to build all ~0.3M ORM rows in a list and `add_all` them, which OOM-killed the seed on a small production container (`exit 137`) — if you see 137, that is the kernel OOM killer, not a Python error.

`just seed-renovation` / `--renovation` **truncates the reference catalogs and the plan** (`RENOVATION_TABLES`: plan rows, plans, reference values) before re-importing; the truncate commits in its own transaction, so a failure during the import leaves those tables empty (the enterprise and user dictionaries survive). Take a backup first, and if it does fail, re-run `just seed-renovation` (the catalogs come back from the static `REFERENCE_OPTIONS`, the plan from the «РБ ПОФ» files). User edits to the working plan are **not** recoverable from the sources — restore the backup for those. `just seed-renovation-plan` / `--renovation-plan` only truncates the plan tables and leaves the catalogs alone.

## Pre-commit (prek)

Root `prek.toml` — builtin hooks (large files, merge-conflict, yaml, eof, trailing, mixed-line-ending `--fix=lf`, fix-byte-order-marker); `back/prek.toml` — ruff format, ruff check --fix, ty check, uv lock; `front/prek.toml` — bun lock, oxfmt, oxlint, bun test, tsc. Run on `git commit` (`prek install`). If pre-commit fixed something itself — recheck the diff and commit again.

### Line endings & encoding (LF, UTF-8, no BOM)

The repo is **LF**: `/.gitattributes` (`* text=auto eol=lf`) normalizes every text file to LF in the index and in working copies, overriding `core.autocrlf` — so editors/tools writing LF never cause "phantom" modifications or "LF will be replaced by CRLF" noise, on any OS.

prek enforces it on commit:
- `mixed-line-ending --fix=lf` — rewrites CRLF to LF in staged text files;
- `fix-byte-order-marker` — strips UTF-8 BOMs.

**Write files as UTF-8 without BOM.** Line endings are then irrelevant — git and prek normalize them.

If you get a new checkout on a machine with `core.autocrlf=true` and files show as "modified" with an empty diff: `git add --renormalize .` + `git checkout -- .` once, and/or set `core.autocrlf=false` (`.gitattributes` makes it moot).

## Languages (standard)

Language is chosen by audience: docs are written for their main reader. This is a hard rule, not a suggestion:

| What | Language | Why |
|---|---|---|
| Identifiers, variable/function names | EN | already the case |
| Code comments and docstrings (new **and existing**) | EN | code is read by agents on every task; a single-language codebase beats a mixed one |
| Commit messages | EN | conventional commits require English headers; body — EN, the Why/What/Verified structure stays |
| Agent documentation (`AGENTS.md`, `docs/*`) | EN | hot path: re-read on every task; EN costs ~1.5–2× fewer tokens for the same meaning |
| User-facing strings: UI labels, toasts, API errors shown to the user | RU | the product is for Russian users — this is content, not documentation. **Never translate these** |
| Future human/developer documentation | RU | read by humans, not agents |
| Log events: event names (`keycloak_identity_received`) | EN | already the case |
| Log events: message text | RU for now | often duplicates user-facing strings; new purely-diagnostic ones — EN |

## Branching and commits (git-flow-lite)

```
main        ← prod. squash-merge from dev + release tag vX.Y.Z; human documentation is updated here
dev         ← integration. squash-merge from feature/*; agent documentation is updated here
feature/*   ← from dev: granular conventional commits (Why/What/Verified)
```

1. **Branches** — short-lived, from `dev`: `fix/<short>`, `feat/<short>` (ascii, lowercase).
2. **Commits** — conventional commits: `type(scope): short English summary`. Type: `feat`/`fix`/`refactor`/`docs`/`test`/`chore`; scope — module (`raw-materials`, `collab`, `auth`, `grid`, `docs`, …). Body — three sections, enough context for an agent without token bloat (header ≤ ~72 chars, body 3–10 lines):

```
fix(raw-materials): paste no longer overwrites foreign rows when sorted

Why: screen index ≠ rows[] index under sorting/filtering; the backend
saw asset "deletions" → FK RESTRICT.
What: mappings moved to getVisibleRows() (paste, cut, copy, presence,
canPasteRange).
Verified: typecheck, oxlint, 16 unit tests; e2e not run (no stack).
```

3. **feature → dev:** only after green checks and user confirmation; merge with `git merge --squash feature/<branch>` — the merge message is a single conventional commit `merge(scope): short summary` + the Why/What/Verified body that distills the branch's commits (they're squashed away, so the message must carry the essentials). The feature branch itself is left in place with its granular history for bisect/revert.
4. **Agent documentation** (`AGENTS.md` + `docs/*`) is updated at this step — not earlier. Exception: landmines in `AGENTS.md` are written down immediately when discovered (one line; easy to lose them by merge time).
5. **dev → main:** squash-merge (release boundary, clean changelog), message — branch summary per the template; tag `vX.Y.Z`; here human documentation is updated (none yet — it's separate from the agent docs and updated only for prod versions).

### Merge checklist (into dev)

- [ ] all checks green (typecheck + lint + tests on both sides);
- [ ] user confirmed the behavior works (for WS/collab — live check; tests don't replace it);
- [ ] `AGENTS.md` and `docs/*` updated for the new behavior;
- [ ] debug files removed (probe scripts, `debug_*.py`);
- [ ] merge message per template `type(scope) + Why/What/Verified`.

## Working principles

- **Minimal changes:** edit existing files, don't create new ones needlessly; follow the file's style.
- **Check before delivery:** typecheck + lint + tests on both sides (table above). After collab changes — `test_collab.py`; after dictionaries/paste — `test_raw_materials.py` + `grid-clipboard.test.ts`.
- **Remove debug artifacts:** probe scripts (`back/tests/debug_*.py`, `front/e2e/*-probe*`) are not committed; the root `.gitignore` silences `*.mjs`/`*.cjs`.
- **Documentation is part of the change:** behavior/timings/protocol changed — update `AGENTS.md` and the corresponding file in `docs/`.
- **Commits** — conventional commits: `type(scope): …` + body Why/What/Verified (template and branching rules — "Branching and commits" above); don't commit or push without the user asking.
- **Git hygiene:** before git operations check `git status` and the current branch (shared repo, foreign edits possible). Don't discard foreign changes.
