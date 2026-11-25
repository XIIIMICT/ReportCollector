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
first sheet whose name starts with «РБ» is read (`:446,458`); other sheets are
ignored, not capped. `docs/TODO.md` still lists "cap scanned rows and issue
reports" as outstanding.

| Limit | Value | Notes | Class |
|---|---|---|---|
| Accepted containers | `.xlsb`, `.xlsx` | 422/JSON `invalid_format` | P |
| Header search window | first 2 rows | template-shaped assumption | D |
| Skipped rows | all-blank rows + one «№ п/п» counting row | no fixed cap | D |
| Asset-less rows | skipped, listed in `skipped_rows`; whole file fails only if all skipped | P | `import_plan.py:604-611,728` |
| Parse / write batch | 200 rows (`_BATCH_SIZE`) | memory/throughput only | D (justified comment) |
| Staging/target UPDATE | one set-based statement per file (Phase 5.1) | no per-row loop | S |
| Export scan / table batches | 5000 / 5000 / 500 rows | memory/throughput only | D |
| Recompute batch | 1000 rows | ~4 min full sweep (documented) | D |
| Preflight cache TTL | 1 h report, 7 d scope | versioned key `7:<flag>` | S |
| Submit issue list | `SUBMIT_ID_ISSUE_ROW_LIMIT = 10` | counts exact, list truncated with «и ещё N» | P (only deliberate issue truncation) |
| Import issue lists | **1000 per category**, env-configurable (`RENOVATION_IMPORT_ISSUE_LIMIT`); exact totals in `issue_counts`, `truncated` flag and a UI banner «показаны первые 1000… всего N» | Implemented Phase 9; boundary tests below/above the cap | S (approved) |
| Error detail | `str(error)[:2000]` | user-facing truncation | P |

## API pagination, filtering, editing

| Limit | Value | Failure | Class |
|---|---|---|---|
| `/plan/rows` `limit` | 1..10000, default 1000 | 422 | D (10000 unjustified) |
| `/plan/facets` `limit` | 1..1000, default 200 (front `FACET_PAGE` 200) | 422 | D |
| `filters` / `sorting` arrays | `max_length=256` each | 422 | D |
| Sort column name | 1..64 chars | 422 | D |
| Filter `values` / search strings | uncapped | — | D — gap |
| Plan cell batch | 1..500; dictionary batch 1..10000 | 422 | D |
| Export columns | 1..64 (KPPK slice 1..16) | 422 | D / P |
| Export `rowIds` | **no count cap** (owner decision 2026-11-25: export everything that exists); duplicates still rejected | generic and plan exports; 4 GiB ZIP ceiling unchanged | P (approved) |
| `MAX_CELL_CHARS` | removed — dead code (owner decision) | Excel still enforces its own limit at open time | D (resolved) |
| 4 GiB ZIP ceiling | `(1<<32)-1` per entry | `ExportTooLargeError` with actionable Russian message | S/P (owner-approved) |

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

Verification: focused boundary tests (`test_plan_import.py`,
`test_collab.py`) plus the full backend suite (690 passed / 13 skipped) and
frontend suite (464 passed); no infra setting changed.
