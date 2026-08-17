# Workflow (environment, quality, tests)

## Environment

1. Copy the config: `cp .env.example .env` (root — single source). To run the front on the host additionally `cp .env.example front/.env.local`.
2. Dependencies: `just deps` (`uv sync --directory back` + `bun install --cwd front`).
3. DB and services: `just db` (postgres+valkey), Keycloak if needed — `just keycloak`.
4. Migrations: `just migrate`. Seed: `just seed` (**recreates the schema!** only with consent, never on prod).
5. Run: `just dev` (compose + hot-reload) or on the host `just back` / `just front`.

About `POSTGRES_HOST`: on the server it's the real DB address (not localhost); `just migrate` uses whatever is in `.env`. In the compose network — `host.docker.internal` or the service name.

**Valkey in the dev stack:** `compose.yaml` overrides `VALKEY_URL=redis://valkey:6379/0` for `back-dev` (the compose service on the same network). Don't point the dev backend at `host.docker.internal` — Docker Desktop's VM loopback times out under load (`redis.exceptions.TimeoutError`) and breaks collab presence/locks. The prod profile keeps `VALKEY_URL` from `.env` (valkey may live on the host there).

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
- `pytest`: `asyncio_mode=auto`, `filterwarnings=error`. **Postgres required**: `back/tests/conftest.py` sets defaults (`postgres/strong_password@localhost:5432/dwh`, schema `rc_test` recreated per test; `VALKEY_URL=""` → MemoryStore for collab). Env can be overridden (`TEST_DATABASE_URL`, `TEST_SCHEMA`).
- Test files: `back/tests/` (`test_collab.py`, `test_raw_materials.py`, `test_approval.py`, `test_local_login.py`, `test_auth_scenario.py`, `test_export.py`, `test_config.py`). Fixtures `client` (HTTP, ASGI), `db_session` (clean seed), `signed_identity_headers` — in conftest.
- Test emails — `@example.ru` (validators reject `.local`/`example.com`).

### Front

- `oxlint --fix`, `oxfmt` (oxc formatter), `tsc --noEmit`.
- **Attention:** `bun format` (oxfmt) may rewrite line breaks in untouched files (regularly touches `src/shared/collab/use-collab-room.ts`, `ws-client.ts`). After format: `git status`/`git diff` — if the diff on foreign files is empty, restore them with `git checkout -- <file>`.
- **A11y label rule (dev-log noise):** every HeroUI/react-aria form control **without a visible `Label` must have an `aria-label`** — `SearchField`, `Autocomplete`, the `SearchAutocomplete` pre-mount `TextField` fallback, etc. Missing it makes react-aria warn on every mount ("If you do not provide a visible label…"). The warning exists only in dev builds (`NODE_ENV !== 'production'`), but it floods `docker logs` and drowns real errors. The label is user-facing → Russian, short (`aria-label="Найти актив"`). Don't chase other a11y rules — only this label rule; visible `Label`s (e.g. in `profile-modal`) and buttons with text are already fine.
- Unit tests — bun test (happy-dom), in `__tests__` next to the code.

### e2e (Playwright)

- Requires a running stack (`just prod` or `just dev`) + seed; `E2E_BASE_URL` (default `http://localhost:5000`), `KEYCLOAK_ENABLED=false` (local login — e2e use it). Specs: `front/e2e/` (`auth.spec.ts`, `raw-materials-ui.spec.ts`, `clipboard.spec.ts`, `collab.spec.ts`, `health.spec.ts`, …).
- **Do not run without the stack** — false failures. The helper probe stand (`front/e2e/probe/`) is removed after debugging.

## Pre-commit (prek)

Root `prek.toml` — builtin hooks (large files, merge-conflict, yaml, eof, trailing); `back/prek.toml` — ruff format, ruff check --fix, ty check, uv lock; `front/prek.toml` — bun lock, oxfmt, oxlint, bun test, tsc. Run on `git commit` (`prek install`). If pre-commit fixed something itself — recheck the diff and commit again.

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
