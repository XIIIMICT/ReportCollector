# back/ — backend and cross-stack data rules

Level-2 guide. Root `AGENTS.md` owns commands and process; `docs/*` owns durable
contracts. Keep one home per fact.

## Domain invariants (server side)

- **КППК** (`/kppk`) copies corporate `raw.kppk_*` tables; the originals are NOT
  in the repo (the operator loads dumps). `just seed-kppk` creates missing
  sources from `back/src/kppk/data/{source_schema.sql,demo_source_rows.sql}` and
  rebuilds «Продуктовые позиции» and the period rows in ONE transaction.
  Unknown product/market text **fails** the copy — no silent skip. `dict` is
  never touched. Text is normalized by `normalize_text`
  (`back/src/kppk/constants.py`): trim, single spaces, NBSP included — the
  dictionaries are keyed by NAME. Periods are rebuilt from Sep 2023 to Dec of
  next year with zero values; the seed TRUNCATES those tables first.
- **Логистика** (`/logistics`) is role-based: administrator (all rows/tabs),
  reviewer (all rates read-only, no dictionaries), asset worker (only granted
  assets, no «Актив» column — the server stamps it), blocked (`is_active` is
  role-derived; a worker with no grants is blocked). Main table `l_rates`: one
  row per asset × route × transport per month. It has FKs into the external
  **`dict`** schema — migration `0035_logistics_rates` fails without it
  (`LOGISTICS_DICT_SCHEMA`, default `dict`; tests use their own schema). Soft
  deletes (`deleted_at`) stay valid and show «УДАЛЕНО: …»; physically deleted
  rows are refused. Every read filters `deleted_at IS NULL`.
- Computed and NOT stored on rates: «Направление» (departure РФ → destination
  market РФ/БЗ/ДЗ, else destination РФ → Импорт, else Экспорт), «Опасный»
  (hazard class set), `rub / 83.90` (constant until the source provides a rate),
  month-over-month net / pseudo-% deviations. Rows are edited in place; only an
  admin deletes them, and the delete is journaled so undo restores the row.
- `l_rates` collaborates like КППК: room `logisticsRates` per period, undo scope
  `logisticsRates/<period>`; the trailing empty row is a LOCAL draft that inserts
  only when every required field is filled, using the period selected then.
- `dict` dictionaries (countries, transport/vehicle matrix, org prefixes) are
  read-only here; `back/dicts.sql`. A missing table returns a demo list with
  `source="fallback"`. Nothing in this application writes to `dict`: no
  migration creates it and no seeder touches it (except `just seed-dev-dict`
  locally/CI). `just seed-logistics` seeds only our side and is idempotent.
- Migrations `0032`–`0035` create the logistics project, price-kind enum,
  hazard classes (`l_hazard_classes`, 1..9) and `l_rates`; app-owned tables use
  the `l_` prefix. Details — `docs/DOMAINS.md`.

## Backend landmines

1. **`AUTH_APP_ORIGIN` — no trailing slash.** CORS origins are derived from it;
   `Origin` never carries a slash → WS handshake 403. `back/src/config.py`
   normalizes, but the correct `.env` value has none.
2. **Emails:** lowercase only and through validators — back `EmailStr` /
   `email_validator`, front `z.email()`. Special-use domains (`.local`,
   `.localhost`, `example.com`) are rejected; use `@example.ru` in tests.
   Lookup by `lower(email)`; Keycloak then finds the existing user.
3. **The business plan is edited only in «Цены»** (`businessPlanQuantityTons`
   etc.); in «Движение» `businessPlanTons` is a non-editable projection. Never
   "project" edits between forms.
4. **Fixing a month:** only the first open month, with no active leases; the
   all-cells-filled requirement was removed 2026-09-04. Preliminary version is
   overwritten, final is immutable; a final fix opens the next month and clears
   the fixed movement scope's undo/redo history.
5. **Undo/redo is per session and form** (`collab_edit_stacks`:
   user/session/scope). «Цены» cannot undo «Движение». `session_id` is a
   page-lifetime id sent with every edit/undo; a page reload starts with empty
   history. Never drop it from PATCH/undo/redo calls.
6. **Presence vs lease:** lease TTL 20 s / renew 10 s = editing; presence TTL
   25 s / heartbeat 15 s = online. Idle >2 min or hidden tab → leases released
   and heartbeat stopped; on return, re-join via ping. Archive «Движение» is
   read-only: no editing, no presence. Acquire/release batch the whole cell set
   into one EVALSHA (NOSCRIPT → one SCRIPT LOAD); partial grants and owner
   checks stay unchanged. The fencing token is PostgreSQL `collab_action_id_seq`
   → `lastActionId`, never a Valkey lease value — see `docs/LEASE-BATCHING.md`.
   Presence joins/selection/leaves are coalesced into one sequenced delta per
   1 s window; a heartbeat publishes no map and a full repair snapshot is due at
   most once per 15 s. Deltas pass the same asset/selection filter as snapshots;
   the reconnect `hello` full snapshot is unchanged. Contract and measurements —
   `docs/COLLAB.md`, `docs/PRESENCE-FANOUT.md`.
7. **«Пользователи» & self-provisioning:** an admin's addition appears in all
   projects (`is_active=false` elsewhere). A user self-registers into ONE
   project by opening its URL; the root URL never provisions anyone (`GET
   /api/me` uses `create=False`), and the first Keycloak login fills the data.
8. **Excel import** (`POST /plan/import/apply`) returns `202` and a durable job.
   `.xlsb` → pyxlsb, `.xlsx` → openpyxl `read_only=True, data_only=True`; the
   upload's own suffix picks the reader and the durable key keeps it. An
   isolated child streams batches of 200 and commits writes + result in ONE
   transaction fenced by the job lease. Reference labels/assets must resolve
   strictly; validation failure rolls back the whole file. Unknown «Код работы»
   appends, blank never clears. Recompute only written rows
   (`recompute_plan_rows(row_ids=…)`). Never add per-row flushes or undo
   journaling. Gate A blocks the form via `GET /plan/import/active` + 409 guard.
   Details — `docs/DATA-JOBS.md`, «Loading the checked file».
9. **Streamed XLSX/ZIP (`back/src/common/xlsx_stream.py`) is byte-fatal.** No
   duplicate `<mergeCell ref>`, no out-of-order `<row r>`, no empty
   `<mergeCells count="0">`; emit the totals row in `__init__` before data;
   dedupe merges first-writer-wins; `row_total` mismatch fails fast with
   `ExportRowCountMismatchError` (the job drops its `.part`). A
   `<dimension ref>` is required by openpyxl read-only mode. Escape the `\x00`
   row placeholder with `xml_specials_escape` (`&<>` only). Sinks must be
   `UnseekableSink` (append-only, data descriptors) and plain ZIP, never zip64:
   the downloader tails a growing `.part` and would otherwise serve the draft
   local header. A part >4 GiB needs a buffered path. Details — `docs/GRID.md` «Misc».
10. **`r_plan_rows` uses semantic names, not `col_NNN` (migration 0041).**
    Physical column order equals form order (`estimate_number` at position 31);
    only `col_002` survives as «Предприятие». Never add a `col_XXX` reference:
    PostgreSQL truncates identifiers to 63 bytes. Old saved filter keys are
    decoded by `decodeLegacyFieldName` in `front/src/shared/api/renovation-shared.ts`.
    Details — `docs/GRID.md` «Semantic field names (migration 0041)».
11. **Cache reads only when the token is cheap and the table is hot:**
    `back/src/common/read_cache.py` — `count(*)` + `max(updated_at)`, never a
    timer. Cached: renovation catalogue, КППК `read_excise_products`.
    Deliberately not cached: «Сырьё»/КППК dictionaries and plan row counts
    (tried and reverted). Tests clear the cache per test.
12. **Logs never carry identity claims or payload text.** `JsonFormatter`
    (`back/src/app_logging.py`) emits only `SAFE_LOG_FIELDS`: correlation and
    bounded classification values. Email, names, OIDC `sub`/issuer,
    `preferred_username`, `email_verified`, raw `Origin`/client id/raw path and
    `error=` exception text are excluded; failures use `error_code`/`error_type`.
    `log_failure()` classifies exception paths without attaching `exc_info`;
    the formatter never calls `formatException()` and never reads a traceback or
    nested cause. `logger.exception()` is forbidden — use the AST/sentinel
    guards in `back/tests/test_log_privacy.py`. Contract —
    `docs/ARCHITECTURE.md` «Logging».

## Seeds and migrations (owner decisions)

- `just seed` recreates the whole schema — ask before running.
- `just seed-kppk` / `just seed-logistics` are scoped as described above;
  `just seed-dev-dict` is **local or isolated CI DB only**, never prod.
- Migration connection has `lock_timeout=30s`. Never edit an applied migration.
- Legacy head `0015_plan_row_col_218` adoption is unsupported after 0041;
  live DB is 0040+. Details — `docs/WORKFLOW.md`.
