# Resource limits — inventory and classification (Phase 9)

This is an **inventory**, not an enforced policy. Classification: **S** =
safety/resource guard, **P** = product/business limit, **D** = implementation
default with no stated justification, **I** = infrastructure/proxy limit
(deferred — do not change here). "Configurable" means an env var exists.

Observed reference data (2026-11-25): the committed technical-directorate
sample is 48,692 bytes on disk, 438,468 bytes uncompressed across 22 ZIP
entries (ratio ~10), largest entry 328,230 bytes; the live working plan holds
261,419 rows and `pg_total_relation_size('rc.r_plan_rows')` is 452 MB. A real
plan export is documented at ~3.3–3.5 GB against the 4 GiB ZIP ceiling
(`docs/DATA-JOBS.md`, «Exported workbook layout»).

## Upload and proxy

| Limit | Value | Failure | Class | Source |
|---|---|---|---|---|
| `RENOVATION_IMPORT_MAX_BYTES` | 256 MiB, env-configurable | 413 «Файл имеет слишком большой размер.» | S | `back/src/config.py:44-48`, `back/src/renovation/router_common.py:64-68,78-83,101-103` |
| `MULTIPART_ALLOWANCE_BYTES` | 1 MiB added to the file cap | 413 before reading the body / while streaming | S | `back/src/common/upload_limits.py:15,23,31-37,53-59` |
| `RENOVATION_UPLOAD_TIMEOUT_SECONDS` | 300 s, env-configurable | 408 «Превышено время загрузки файла…» | S | `back/src/config.py:49-51`, `upload_limits.py:39,48` |
| Hash / staging chunk | 1 MiB (`_UPLOAD_CHUNK_BYTES`) | none — bounds RAM | D | `router_common.py:61,71-72,90-91` |
| Starlette multipart defaults | spool 1 MiB, non-file part 1 MiB, 1000 files/fields | framework 400 | D | Starlette 1.10.0 upstream defaults; not overridden in repo |
| Front proxy timeout | 24 h (`BACKEND_TIMEOUT_MS`) | Node `UND_ERR_HEADERS_TIMEOUT` avoided | D (justified) | `front/src/shared/api/backend-proxy.ts:14-18` |
| Front proxy body | whole upload in one `Buffer`, no size check | memory spike bounded only by the backend 256 MiB | D | `backend-proxy.ts:37,51-53`; streaming deferred, `docs/TODO.md` |
| nginx `client_max_body_size` | **not set** → 1 MiB default | 413 before the app | **I — deferred** | `infra/nginx/default.conf.template`; front handles contentless 413 |
| nginx proxy read/send | 24 h (WS 1 h) | 504 avoided | **I — deferred** | `infra/nginx/default.conf.template:75,91-92` |

## Workbook parsing and import

No row/column/cell/sheet-count caps exist in either reader; `.xlsb` and `.xlsx`
are streamed row by row (`back/src/renovation/import_plan.py:439-465`). Only the
first sheet whose name starts with «РБ» is read; other sheets are
ignored, not capped. Since RC-3 the *diagnostics* are bounded too: every issue
category is counted incrementally and keeps at most `issue_limit` examples, and
whole-file duplicate detection uses an OS-backed sorted spool rather than an
O(rows) Python list. No row/column/cell cap was introduced — the parser still
streams everything.

| Limit | Value | Notes | Class |
|---|---|---|---|
| Accepted containers | `.xlsb`, `.xlsx` | 422/JSON `invalid_format` | P |
| Header search window | first 2 rows | template-shaped assumption | D |
| Skipped rows | all-blank rows + one «№ п/п» counting row | exact count, bounded examples | D |
| Asset-less rows | skipped, listed in `skipped_rows`; whole file fails only if all skipped | P | `import_plan.py` |
| Parse / write batch | 200 rows (`_BATCH_SIZE`) | memory/throughput only | D (justified comment) |
| Duplicate spool run | 20 000 `(code, row)` pairs | disk-backed sorted runs + k-way merge; exact duplicate/new/update totals, bounded examples | S (RC-3) |
| Preflight existing-code oracle | 8192 digests/run, 1 MiB bloom, 32-way merge fan-in | working plan's codes live in a sorted fixed-width digest file; no Python set/list of codes; DB read uses 5000-row keyset pages | S (RC-2) |
| Staging/target UPDATE | one set-based statement per file (Phase 5.1) | no per-row loop | S |
| Apply code/index sets | transaction-local temp tables (`plan_import_existing`, `plan_import_ambiguous`, `plan_import_seen`, `plan_import_written`) | no O(rows) Python sets/lists; `ON COMMIT DROP`, no migration | S (RC-3) |
| Export scan / table batches | 5000 / 5000 / 500 rows | memory/throughput only | D |
| Recompute batch | 1000 rows | written ids streamed from `plan_import_written`; full sweep is the backfill | D |
| Preflight cache TTL | 1 h report, 7 d scope | versioned key `8:<flag>` | S |
| Submit issue list | `SUBMIT_ID_ISSUE_ROW_LIMIT = 10` | counts exact, list truncated with «и ещё N» | P (only deliberate issue truncation) |
| Import issue lists | **1000 per category**, env-configurable (`RENOVATION_IMPORT_ISSUE_LIMIT`); exact totals in `issue_counts`, `truncated` flag and a UI banner «показаны первые 1000… всего N» | Phase 9; RC-3 makes collection bounded while counting (no collect-then-slice) | S (approved) |
| Duplicate example rows | first 10 sheet rows per code, then «и ещё N» | keeps an actionable example bounded even when one code repeats thousands of times | S (RC-3) |
| Error detail | `str(error)[:2000]` | user-facing truncation | P |

## API pagination, filtering, editing

| Limit | Value | Failure | Class |
|---|---|---|---|
| Legacy full `GET /plan` | **5000 rows** (`FULL_PLAN_READ_MAX_ROWS`, aligned with `INLINE_EXPORT_MAX_ROWS`) | 422 with fixed code `PLAN_FULL_READ_TOO_LARGE`; no rows returned | S (resource guard, not a product cap) |
| `/plan/rows` `limit` | 1..10000, default 1000 | 422 | D (10000 unjustified) |
| `/plan/facets` `limit` | 1..1000, default 200 (front `FACET_PAGE` 200) | 422 | D |
| `filters` / `sorting` arrays | `max_length=256` each | 422 | D |
| Sort column name | 1..64 chars | 422 | D |
| Filter `values` / search strings | uncapped (no product cap — owner approval required) | 422 only for a malformed filter SHAPE; no app-level 413 for body size | D — gap, reviewed RC-6 |
| Plan cell batch | 1..500; dictionary batch 1..10000 | 422 | D |
| Export columns | 1..64 (KPPK slice 1..16) | 422 | D / P |
| Export `rowIds` | **no count cap** (owner decision 2026-11-25: export everything that exists); duplicates still rejected, including on plan exports | generic and plan exports; 4 GiB ZIP ceiling unchanged | P (approved) |
| `MAX_CELL_CHARS` | removed — dead code (owner decision) | Excel still enforces its own limit at open time | D (resolved) |
| 4 GiB ZIP ceiling | `(1<<32)-1` per entry | `ExportTooLargeError` with actionable Russian message | S/P (owner-approved) |

### Legacy full-plan read compatibility (RC-6)

`GET /api/renovation/plan` is kept as a compatibility read for small plans. It
builds one Pydantic row model with ~215 values per row, so an unbounded read of
the 261,419-row live plan would materialize tens of millions of Python objects
in the request process. `read_plan` now fetches `LIMIT 5001`: the extra row is
the overflow signal. At or below 5000 rows the historical `RenovationPlanRead`
response is unchanged; above it the server refuses the WHOLE request — it never
returns a truncated plan and never builds response row models for a refused
plan. 5000 is not a new product number: it reuses the owner-approved
`INLINE_EXPORT_MAX_ROWS` synchronous-work budget (above).

The error contract is stable and machine-readable: HTTP **422** with
`detail = {"code": "PLAN_FULL_READ_TOO_LARGE", "message": "<Russian, names the
limit and the alternative>", "maxRows": 5000, "alternative": "/plan/rows"}`
(`PlanFullReadTooLargeDetail` in `src/renovation/schemas.py`). Clients should
match on `code`, then page through `GET /plan/rows` (or `POST /plan/rows/query`)
with `offset`/`limit`. This is a documented deprecation path, not a removal:
the application itself already reads rows only through the paged endpoints; the
`front/src/shared/api/renovation-plan-api.ts` `getRenovationPlan` wrapper is a
compatibility export with no in-app callers. External clients cannot be ruled
out, so small-plan full reads keep working.

### Filter values / search strings (RC-6 review)

`RenovationColumnFilter.values`, facet search strings and search bounds remain
**uncapped**: a 5001-value filter is an existing, passing test case
(`test_large_filter_body_and_validation`) and no measured/realistic in-repo
abuse case justifies a new product maximum. The owner has not approved a value
count cap, so none was added. Stable behavior stays as follows: an invalid
filter shape (e.g. a range on a text column) is 422 with a Russian message;
JSON request bodies have no application-level size check; the front proxy still
buffers the whole body; and nginx `client_max_body_size` remains unset (1 MiB
default, 413 before the app) — infrastructure is deferred and out of scope
here. This stays a documented class-D gap, not a new silent limit.

### `rowIds` exact-set export (RC-6 confirmation)

Neither the generic nor the plan export caps the number of `rowIds`; a plan
export with 5001 distinct IDs is accepted and exports exactly the rows that
exist. Duplicates are still rejected with the shared Russian message
`Список экспортируемых строк содержит дубли.` (the plan request now validates
this in `RenovationPlanExportRequest`), and the 4 GiB ZIP/XLSX ceiling is
unchanged. Neither restriction is a row-count cap.

## Jobs, leases and collaboration

| Limit | Value | Failure | Class |
|---|---|---|---|
| Active jobs / per user | 8 global, 2 per user | 429 «Очередь заполнена…» | S |
| Job TTL / artifact TTL | 3600 s | 404 after expiry | S |
| Lease / attempts | 30 s, 3 attempts, backoff 2/4 s | retry / fence / failed | S |
| Worker timeouts | import 4 h, export 1 h | `generation_failed` | S |
| Worker concurrency | export 1 (≤8), import 1 (≤4), env-configurable | validation error | S |
| WS outbound queue | 256 messages/connection | close 1013 `slow_consumer` | S |
| WS frame / rate | 64 KiB, 300 messages / 10 s | close 1009 / 1008 | S |
| Lease cells / owned leases | 500 / 1000 | **explicit WS error** since Phase 9 (no silent slice); presence selection >1000 → «Некорректное выделение.» | D → S (approved) |
| Undo stack / scan / TTL | 500 actions, 200 scanned, 24 h | oldest dropped | S / D |
| Auth rate limit | 20 requests / 60 s | 429 + `Retry-After` | S |
| Room members / sockets | no limit | — | D — gap |

## Owner decisions (2026-11-25)

- Issue reports: a generous **1000 per category** cap with exact `issue_counts`
  and a `truncated` flag; env override `RENOVATION_IMPORT_ISSUE_LIMIT`.
- `MAX_CELL_CHARS`/`truncate()` were dead code — **removed**, no cell limit introduced.
- Export `rowIds`: **no count cap** anywhere (the owner wants everything exported);
  duplicate IDs and the 4 GiB ZIP ceiling remain.
- WS lease caps: an oversized selection is an **explicit error**, never a silent slice.
- All other class-D values (page/facet limits, filter/sort arrays, batch sizes,
  worker timeouts) are **unchanged**; their values are recorded above.
- Parser row/column/cell/sheet caps: **not introduced** — readers stream and no
  evidence supports a business maximum.
- Infrastructure/proxy limits remain deferred and untouched.

### RC-2 decision (2026-11-27)

- No arbitrary row cap and no product limit: the working plan's codes are
  indexed on disk instead of in a Python `set`. `ExistingCodeOracle` hashes each
  code to a 32-byte SHA-256 digest, external-sorts the digests in temporary
  files (8192-digest runs, at most 32 open runs per merge pass) and answers
  `contains(code)` from a read-only mmap of the sorted file. A fixed 1 MiB bloom
  filter (7 hashes) short-circuits absent codes; a bloom false positive only
  costs a binary search and never changes a verdict.
- The **unavoidable bounded buffers** are the 1 MiB bloom bitmask, one 8192 ×
  32-byte sort run (~256 KiB), the 5000-row DB keyset page, the 20 000-pair
  duplicate spool from RC-3, and the `issue_limit` example lists. The mmap of
  the digest file is file-backed and pageable; its bytes are not Python heap.
  Full SHA-256 digests make a collision-induced wrong verdict ~1e-65 likely for
  a 300k-code plan (the same practical-exactness assumption as the upload hash).
- The request DB connection is released after the code page reads and the
  reference snapshot, before the CPU-bound workbook parse; the oracle is closed
  in a `finally` (including `CancelledError`) under a lock so a detached
  `to_thread` parse cannot touch an unmapped file.
- Measurement (`back/tests/test_existing_code_oracle.py`, synthetic 150k-code
  plan and 300k-row workbook, one Windows dev machine, `tracemalloc` covering
  index build + parse): bounded oracle traced peak **6.1 MiB** and RSS delta
  **13.3 MiB**, versus the old set-based path's **16.3 MiB** traced peak and
  **30.3 MiB** RSS delta. Exact counts matched: 200k updates, 100k new rows,
  100k duplicate codes. Traced wall time (77.6 s vs 41.6 s) is inflated by
  millions of tiny mmap slices being traced; without tracing the index build
  takes **1.1 s** and 300k membership tests **3.8 s**. No production data or
  multi-worker contention was measured.

### RC-3 decision (2026-11-26)

- No parser row/column/cell/sheet caps: the readers still stream. The resource
  guard is on *retained* data: bounded issue examples with exact counters, a
  disk-backed sorted duplicate spool and transaction-local code/id sets in
  apply. A workbook of any size can be analysed without materializing an
  O(rows) Python collection in the worker.
- Apply parses (and validates) into a temporary spool BEFORE opening the DB
  session, the write transaction or the project gate; the mutation itself is
  still ONE transaction with the lease-fenced job result in the same commit.
- Measurement: `back/tests/benchmark_import_bounded_memory.py` (synthetic
  workbook/plan only). See `docs/DATA-JOBS.md` for the before/after numbers and
  their limits. No production figures are claimed.

### RC-6 decision (2026-11-26)

- Legacy full `GET /plan`: **5000-row compatibility budget**, reusing the
  approved inline-work number; larger plans get a stable 422 code
  `PLAN_FULL_READ_TOO_LARGE` and must page through `/plan/rows`. No truncation.
- Filter `values` / search strings: **no cap introduced** (no owner approval, no
  measured abuse case); request-body/transport protection stays deferred with
  infrastructure. Documented gap, not a new limit.
- Export `rowIds`: **still no count cap**; duplicates and the 4 GiB ceiling stay.
- Infrastructure/proxy limits remain untouched.

Verification (RC-6): `tests/test_plan_read_compat.py` (boundary at/above 5000,
zero row models materialized on refusal, stable error shape, paged alternative,
uncapped and duplicate `rowIds`) plus targeted plan/export/router tests; the
full backend suite and static checks are recorded in the commit body.

### RC-5 measurement scope (2026-11-27)

No new resource limit was introduced. The load harness
(`back/tests/benchmark_rollout_workload.py`) is a **measurement** tool: it
creates and drops only a disposable `rc_workload*` schema and reports the
observed p50/p95/p99, RSS/CPU, pool-acquire and Lock-wait samples for a
synthetic 306k-row plan. The captured run lives in
`docs/ROLLOUT_WORKLOAD_2026-11-27.json` and is CI-stand evidence only. KPPK
slices, collaboration/WS fan-out, worker restart under a supervisor, queue wait
under load, dropped sockets and the full 306k-row workbook download need the
target host and are recorded as OPEN, with no invented numbers. The documented
class-D gaps (filter `values`/search strings uncapped, no room/socket limits,
proxy body buffering) are unchanged.
