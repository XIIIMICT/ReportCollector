# Renovation design: modules and query cost

Implementation boundaries and measured query behavior moved from
`ARCHITECTURE.md` (Phase 8 split).

## Renovation service module boundaries (Phase 4)

`back/src/renovation/service.py` is now a **façade**: it keeps the historical
`from src.renovation.service import X` contract (router, collab, scripts and
tests import ~25 names from it) while the implementations live in flat
sibling modules, one-way dependency `service → plan_reads → service_common`:

- `service_common.py` — shared primitives with NO dependency on the rest of
  the service: exceptions, error singletons/factories, audit/ID constants,
  plan-row read helpers, renovation-ID helpers, reference-label resolution,
  cell rule/coercion helpers. Both other modules import from here.
- `plan_reads.py` — the READ surface: access resolution, meta, plan rows,
  facets, summary, versions, diff and the streaming export queries. No writes.
- `recompute.py` — formula materialization (`_materialize_row`,
  `_recalculate_row`) and the `recompute_plan_rows` backfill. Pure computation
  over rows within the caller's session; no HTTP or authorization.
- `cell_mutations.py` — the WRITE surface for cells and rows: create/delete/asset
  assignment, `update_plan_cells`, undo/redo `apply_renovation_cell_value` and
  reference coercion. `@project_write`, row locks, lease checks, journal writes
  and revision bumps stayed at their original call sites.
- `lifecycle.py` — `submit_plan` / `approve_plan` and the next working copy.
- `service.py` — a thin façade (46 lines) re-exporting the ~25 historical public
  names so router, collab, scripts and tests keep importing from one place.

Dependency direction is one-way: `service → {plan_reads, recompute,
cell_mutations, lifecycle} → service_common`; `plan_reads → service_common`.
No module imports back into `service`; `export.py`'s deferred import of
`RenovationPlanExportData` is preserved to avoid a cycle. The split is
extraction-only: transaction ownership, locking boundaries and authorization
checks stayed at the same call sites; the full backend suite (677 passed /
13 skipped) is the no-behavior-change gate. `src/common/job_contracts.py`
similarly owns the generic durable-job limits and `ExportQueueFullError`,
re-exported by `common/export_jobs.py`.

### Formula engine arithmetic (RC-4)

`recompute.py` materializes derived plan fields through `formula_engine()` on
edits, imports, the backfill and the export's missing-value path. Numeric
formulas now evaluate as `Decimal` end-to-end (28-digit division,
`ROUND_HALF_UP` to the declared precision, normalized negative zero, bounded
non-finite/overflow errors); date math stays integer days. Dependency order,
missing-value semantics and error texts are unchanged. The missing legacy
`formula.rs` evidence and the full rounding decision live in
`docs/DATA-JOBS.md`, «Renovation formula arithmetic»; all 133 form formulas are
exercised by `back/tests/test_renovation_decimal_formulas.py`.

`router.py` (1935 -> 27 lines) is now just the aggregator: it creates one
`APIRouter` and `include_router`s six sub-routers with the shared
`/renovation` prefix — `router_import`, `router_plan`, `router_export`,
`router_lifecycle`, `router_dictionary`, `router_undo` — plus
`router_common` holding auth dependencies and upload/scope helpers. Paths,
methods, dependencies and response models are unchanged (verified by diffing
`/openapi.json`: 36/36 renovation paths identical); the private helpers that
tests monkeypatch are re-exported from `router.py` for back-compat, and the
three import tests now target `router_import`.

`router_import` resolves the working plan before validating an upload: existing
work codes are read with `plan_id = :working`, and the cache scope carries that
plan id plus `r_plans.revision`. Migration 0043 maintains the revision
with statement-level triggers on `r_plan_rows`, so every insert/delete, every
`plan_id` transfer and every `work_or_repair_code` change (cell edit,
undo/redo, import, recompute, script)
invalidates the preflight atomically; an archived-only code is new in a newer
version, while edits in an archived plan cannot invalidate the working one.
RC-1: a row moved between plans with an UNCHANGED `work_or_repair_code` still
changes both code sets, so the statement-level UPDATE trigger bumps BOTH the
old and new plan from the transition tables (one increment per plan per
statement). `back/tests/test_plan_revision_trigger.py` pins that contract on
the real migrated disposable schema.
The old `count(*) + max(updated_at)` token missed non-latest deletes; the cache
version moved from `7` to `8` so entries using it are never served.
`import_apply` enforces the same rule independently in the worker's
transaction — workbook duplicates across batch boundaries and codes already
duplicated in the target plan are refused, applies of one plan are serialized by
a plan-scoped advisory lock, and the set-based `UPDATE ... FROM` writer stays.
RC-3 moved workbook parsing/spooling out of the write transaction: the worker
parses into a temporary spool first, then mutates in one short transaction with
the gate held only for apply. Existing codes, seen codes, ambiguous codes and
written row ids are transaction-local temp tables instead of O(rows) Python
collections; recompute pages over written ids in bounded batches. The preflight
counts issues incrementally with bounded examples and detects whole-file
duplicates through an OS-backed sorted spool (exact totals, no row caps). Phase
timings use `observe_phase`; the synthetic harness and before/after numbers are
in `docs/DATA-JOBS.md`.
The intended DB constraint is `UNIQUE (plan_id, work_or_repair_code)` on the
semantic 0041 column (`col_003` is gone); migration 0042 is authored but its
rollout is BLOCKED pending the read-only audit and live-data cleanup described
in `DATA-JOBS.md`; migration 0043 was corrected in place before rollout and
inherits that same gate.

`front/src/shared/api/renovation.ts` (1140 -> 11 lines) is now a barrel
re-exporting six workflow modules: `renovation-shared.ts` (types, constants,
`decodeLegacyFieldName`/`decodeLegacyFieldKeys`), `renovation-import-api.ts`,
`renovation-plan-api.ts`, `renovation-export-api.ts`,
`renovation-dictionary-api.ts` and `renovation-undo-api.ts`. All dependencies
are leaf modules, so there are no cycles; the 82 historical exports are
identical (checked), and the runtime request/response shapes are unchanged.
`bun test` (461 passed) and `bun check` are the no-behavior-change gate.

## Facet and query cost (Phase 6 measurements)

`read_plan_facets` used to materialize **every distinct value** of a column in
Python and then filter, casefold-sort, count and slice there. All generic
columns now do that in ONE grouped SQL scan: the display bucket is a SQL
expression (number formatting, date ISO, boolean, reference label via a LEFT
JOIN, asset name, audit minute in UTC), casefolding is `lower` + ß/ẞ expansion,
and window functions return the total/matching/empty-bucket counts alongside
the page — so a returning page never pays a second grouped scan. An empty page
falls back to one aggregate. The specialized `work_or_repair_code` path is
unchanged.

Measured on the live dev plan (261 419 rows) before → after:
`defect_list...` 436 → 168 ms, `work_description` 540 → 268 ms,
`equipment_unit_name` 260 → 160 ms; the last two with a text search 582 → 447
ms and 192 → 206 ms. Low-cardinality fields stayed within noise. The earlier
attempt with a 100-character `translate()` and three grouped scans was slower
than the Python path and was replaced; `lower` + ß handling and one scan fixed
that. No exact-count or ordering behavior changed (all facet tests pass).

A bulk cell PATCH prefetches reference ids once (Phase 6.2) instead of one
catalog SELECT per cell; see the import/export section above.

Page reads still issue ONE count query (filtered total, whole-plan total,
position spread and the anchor offset) and keep exact totals: 89 ms at the head
and 41 ms at a 150k offset on the live plan. No time-only count cache and no
read-path `data_revision` was introduced — both would need an inventory of every
mutation path (imports, edits, Undo, recompute, lifecycle, scripts) and an
owner-approved consistency design. The **preflight-only** generation added by
0043 is a different, narrow token: it lives on the plan row and is maintained by
DB triggers precisely so no mutation path has to be enumerated in application
code; it keys the optional Valkey report cache, not grid reads.

### Legacy full-plan read compatibility (RC-6)

`GET /renovation/plan` is a compatibility endpoint: it returns a whole plan
version (`RenovationPlanRead`) in one response, including one Pydantic row model
with ~215 values per row. On the 261,419-row live plan that is an unbounded
allocation inside the request process. It is now bounded at
`FULL_PLAN_READ_MAX_ROWS = 5000` (the owner-approved `INLINE_EXPORT_MAX_ROWS`
inline-work budget — no new product number). `read_plan` fetches
`LIMIT 5001`: the extra row is the overflow signal. At or below the bound the
response is unchanged; above it the whole request is refused BEFORE any row is
converted to a response model — no truncation, no partial plan.

The refusal is a stable contract: HTTP 422 with
`detail = {"code": "PLAN_FULL_READ_TOO_LARGE", "message": "<Russian>",
"maxRows": 5000, "alternative": "/plan/rows"}` (schema
`PlanFullReadTooLargeDetail`, router translation in `router_plan.py`). Clients
match on `code` and migrate to `GET /plan/rows` / `POST /plan/rows/query` with
`offset`/`limit`. This is a documented deprecation path, not a removal.

Consumer evidence: the application itself loads only paged windows
(`getRenovationPlanRows`, `getRenovationPlanSummary`, `getRenovationPlanFacets`
in `renovation-workspace.tsx`); `getRenovationPlan` in
`front/src/shared/api/renovation-plan-api.ts` has no in-app callers and is kept
as a compatibility export; the smoke spec's `/api/renovation/plan?` wait is a
PATCH, not a full read. Backend tests and scripts call the endpoint/router, and
external clients cannot be ruled out — hence small-plan full reads stay intact.
`read_plan_export_data` / `iter_plan_export_rows` are a separate path: exact-set
`rowIds` exports remain **uncapped** (owner decision), duplicates are rejected
in `RenovationPlanExportRequest`, and the 4 GiB ZIP ceiling is unchanged.
Filters and search strings stay uncapped — no owner-approved value cap and no
measured abuse case; transport limits remain deferred. Full contract and test
inventory: `docs/LIMITS.md`, `tests/test_plan_read_compat.py`.

Sorted windows still use SQL `OFFSET` (the grid has infinite scroll plus a
"go to №" jump, so a cursor would change the API/UI contract). Measured on the
live plan: default deep 41 ms, `work_description` sorted head 827 ms / deep
3 812 ms, `work_cost_rub_ex_vat` deep 3 169 ms. A covering index including ALL
sort keys `(plan_id, lower(col), col, position, id)` brought these to 110 ms /
1 368 ms and 830 ms, but the owner chose to keep the current write cost and
record the option instead of adding indexes or switching to a cursor. The
measurement helper is `back/src/scripts/explain_backend.py`.

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

A full plan export is ~306k rows × 217 columns: minutes of CPU and ~500 MB of
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
   `JobRepository.claim(kind, concurrency=…)` counts **only running jobs of that
   kind**, so an import worker's slots cannot be consumed by a plan export (or a
   table query export) and vice versa. The `MAX_ACTIVE_JOBS` global cap and the
   per-user `MAX_ACTIVE_JOBS_PER_USER` cap are separate explicit admission limits
   enforced in `JobRepository.create()`, not an accidental side effect of one
   manager's per-kind limit. Lease expiry/retry, fencing tokens and stale-worker
   completion protection are unchanged.
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
  missing derived values must preserve editable `work_or_repair_code` work codes; its old
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
