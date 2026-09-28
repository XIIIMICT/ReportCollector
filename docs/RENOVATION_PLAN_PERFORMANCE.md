# Renovation plan at scale (2026-09-28)

## Scope and method

Assessment of the «Пообъектный план» read/write path against the reseeded
production-shaped dataset, with the goal of keeping the grid usable as the plan
grows. Measured against the live dev stack (`rc-postgres`, database `dwh`,
schema `rc`, fresh seed) using `psql` and direct calls into
`src/renovation/service.py` — not estimates.

Everything below is a measured number unless marked otherwise. Latencies are
warm-cache unless stated; the first touch after an import reads ~27 MB of index
pages and is 3–10× slower.

## Measured baseline

### Dataset shape

| Metric | Value |
|---|---|
| Working plan rows | 306 140 |
| Columns on `renovation_plan_rows` | 225 (217 form fields + 8 service columns) |
| Heap / indexes / total | 289 MB / 62 MB / 352 MB |
| Assets visible to an admin | 17 |
| Reference values | 83 |
| Plans | 1 (working) |
| Non-empty fields per row (average) | **19.5 of 217 (~9%)** |

The plan is ~91% empty. That single fact drives most of the findings below: the
storage layer handles the emptiness cheaply (≈4.4 bytes per cell), but the API
layer does not.

### Query cost

| Query | Cost |
|---|---|
| `count(*)` with the full visibility predicate (cold) | ≈516 ms |
| `count(*)` with the full visibility predicate (warm) | ≈35 ms |
| `count(*) WHERE plan_id = ?` (warm) | ≈124 ms |
| `count(DISTINCT asset_id) WHERE plan_id = ?` (warm) | ≈38 ms |
| 1000-row window, full join and ordering | ≈2.6 ms |
| Facet group-by on `col_011` (text) | **≈412 ms, 79 935 distinct values** |
| Facet group-by on `col_023` (number) | ≈410 ms, 31 153 distinct values |

SQL is not the bottleneck. The window fetch is ~3 ms; the count is tens of
milliseconds warm. The expensive parts are the facet aggregation and the
response payload.

### Read path cost (service call, as the API uses it)

| Call | Service time | JSON payload |
|---|---|---|
| Window, 300 rows | 63–192 ms | **1.21 MB → 0.33 MB** (after the change below) |
| Window, 1000 rows | 149–232 ms | **4.06 MB → 1.12 MB** (after the change below) |

The first grid render issues two sequential requests (a `limit=1` probe to learn
`total`, then a 300-row tail chunk). Each further scroll chunk is another
request. So a single user session transfers several MB per screenful.

## Assessment

### Adequate for this scale

- **Windowed reads.** `GET /plan/rows` paginates (`offset`/`limit`, cap 10 000)
  and the grid holds a 300-row window anchored at the plan tail, so the browser
  never holds the whole plan.
- **Server-side filtering.** Header filters are translated to SQL and ANDed in
  the row query, so `offset`/`limit` and «Найдено» count only matching rows.
- **Indexes that matter.** `(plan_id, asset_id, position)` and
  `(plan_id, position)` support the tail-anchored window and the visibility
  predicate; the reference FK columns are indexed.
- **Streaming export.** The XLSX writer streams through a queue instead of
  building a workbook in memory.
- **Sparse storage.** Postgres stores the 306 140 × 217 matrix in 352 MB total.

### Not adequate for this scale

1. **The window payload carried every column, most of them null.** 19.5 non-empty
   fields per row still produced 217 keys, ~4 KB per row, 4 MB per 1000-row
   window. The grid shows a subset of columns at a time and renders null and
   missing identically, so ~80% of the bytes were placeholders.
   *Fixed in this pass — see below.*
2. **Facets aggregate the whole plan and sort in Python.** `read_plan_facets`
   runs one `GROUP BY` over 306 140 rows, collapses NULL/'' in Python, sorts all
   distinct values, then slices a 200-value page. For `col_011` that is 412 ms
   and a Python dict of ~80 000 entries — to show 200 options. A filter popover
   with 80 000 values is also unusable in the UI regardless of cost.
3. **`GET /plan` has no row cap.** `read_plan` loads every row of the version into
   Python objects and serializes them. At 306 140 rows that is roughly 1.2 GB of
   JSON before the sparse change (and ~270 MB after). The frontend calls it for
   the archive-comparison plan (`renovation-workspace.tsx`, `comparedToPlanId`
   branch); with a large archive this is an out-of-memory risk, not a slowdown.
   The other call site (the "complete window" refresh fallback) is unreachable —
   `complete` is only ever set inside the branch that requires it to already be
   true.
4. **Two to three `COUNT(*)` per window request.** One for `total`, one for
   `totalAll` when the asset filter narrows. Warm this is tens of milliseconds;
   cold, half a second each. Neither is cached, and the unfiltered total is
   derivable from plan metadata.
5. **The export materializes everything before streaming.** `read_plan_export_data`
   builds the full row list (and runs `engine.materialize(values)` per row)
   before the writer thread starts, so peak memory scales with the plan.
6. **Per-row Python work.** `_plan_row_read` iterates 217 metadata entries per
   row and recomputes the renovation ID per row, for every window.
7. **No per-plan metadata.** Row counts are recomputed with a `GROUP BY` in
   `read_plan_versions`; there is no stored `row_count` to serve «всего» for
   free.
8. **`_reference_labels` and `_baseline_boundary` run per request** instead of
   per plan version.

## Optimization plan

Ordered by measured impact per unit of risk. Items marked ✅ are implemented in
this pass.

### 1. Response shape (biggest lever, no schema change)

- ✅ **Omit empty cells from `values`.** The grid treats a missing key exactly
  like a null (all its cell renderers and copy/parse helpers test
  `value === null || value === undefined || value === ""`). Measured: 4.06 MB →
  1.12 MB for 1000 rows, 1.21 MB → 0.33 MB for 300 rows.
- ✅ **Never build null placeholders** — `_plan_row_read` now skips before the
  per-field transform, cutting per-row Python work in proportion.
- **Next: column projection.** `GET /plan/rows` should accept the same optional
  `block` the export already accepts (`_export_fields` in `export.py`). Block
  sizes: `tech` 32 fields, `mto` 18, `ksir` 173, no block 217. This is the only
  lever that helps `ksir` users materially, and it composes with the sparse
  payload (32 fields × 19 non-empty ≈ a few hundred bytes per row). **Deliberately
  not enabled client-side yet:** the grid currently renders every block's columns
  (only editing is restricted by `userBlock`), so projecting would blank cells the
  user can currently see. Wire it together with a grid column-visibility decision.

### 2. Facets

- **Search-driven facets instead of full value lists.** Add a `query` parameter
  that filters the grouped values server-side (`ILIKE` on the raw column or its
  resolved label) with a hard `LIMIT`, and return `total` as an estimate or drop
  it. Removes the 412 ms full-table aggregation and the 80 000-entry Python dict
  from the common path.
- **Precompute per-column facets** into a small table
  (`renovation_plan_facets(plan_id, column, value, count)`), rebuilt on
  import/submit and updated incrementally on cell edits. Turns a facet request
  into an indexed lookup on a table with ~10²–10⁵ rows instead of 306 140.
- **Cap the UI**: past a few hundred distinct values, the popover should require
  a search term before listing anything.

### 3. Cost removal on the hot path

- **Cap `GET /plan`.** Give `read_plan` a row limit and return a clear error
  above it (mirroring `MAX_EXPORT_ROWS` in the materials export), or delete the
  endpoint in favour of `/plan/rows` and the diff. This turns a potential OOM
  into a deterministic error.
- **Store `row_count` on `renovation_plans`**, maintained inside the same
  transaction as add/delete/import/submit/approve. Then `totalAll` and the
  unfiltered `total` are free, and the `limit=1` probe request disappears.
- **Cache `total` per (plan, filter signature)** in process, invalidated by
  writes, for the filtered case.
- **Cache `_reference_labels` and `_baseline_boundary`** per plan version
  instead of per request.
- **Cover the facet columns that are actually used.** Today only the reference
  FKs and `position` are indexed; the columns users facet on (text/number/date)
  force a sequential scan. Add indexes for the high-traffic facet columns, or
  rely on the materialized facet table above.
- **Stream the export from a cursor.** Replace the materialized row list with
  `stream_results` execution feeding the writer queue, and recompute formulas
  lazily per row.

### 4. Data structure

- Keep the wide sparse table for now: 352 MB total for 306 140 × 217 is
  acceptable, and every alternative (EAV: ~66 M value rows) is worse.
- **Split the hot tail from the long tail** if and when the plan doubles:
  keep the filtered/sorted/indexed columns as real columns and move the rest
  into one `jsonb` payload column. This shrinks the heap, improves the
  cache locality of the count and facet scans, and makes column changes cheap.
  Do it only after the response-shape and facet work, because those remove most
  of the pain without a migration.
- **Partition by plan version** (`plan_id` or state) so archived plans stop
  inflating the working plan's table, indexes and vacuum cost.
- Revisit the five global reference-FK indexes: they are not plan-scoped, and
  archive versions duplicate the same values. Verify with `pg_stat_user_indexes`
  before dropping.

### 5. Frontend

- **Remove the full-plan fallback** in the refresh path and give the archive
  comparison a paged implementation on top of `/plan/rows?planId=…`. This is the
  single change that most reduces the worst-case browser memory.
- **Cache facet results** per (plan, column) in memory, and fetch a facet only
  when its popover opens (already the case) — plus debounce the search.
- **Memoize row objects and cell renderers** so a collab heartbeat does not
  rebuild every cell closure in a 2 200-line grid component.
- Consider raising `WINDOW_CHUNK` for small filtered result sets and lowering it
  for the unfiltered 306 140-row view: today it is a constant 300.

## Verified targets

| Step | Target |
|---|---|
| 1000-row window payload | ≤ 1.2 MB (measured: 1.12 MB, was 4.06 MB) |
| 300-row window payload | ≤ 0.35 MB (measured: 0.33 MB, was 1.21 MB) |
| Facet request | ≤ 60 ms for any column (today 412 ms on `col_011`) |
| Window requests per first render | 1 (today 2 serial) |
| `GET /plan` | never more than one window of rows |

Full latency targets for the endpoints (p50/p95/p99) still need to be agreed —
see the open questions in `docs/AUDIT-2026-09-28.md`.

## Verification

- `uv run --directory back pytest -q tests/test_renovation_api.py` — 63 passed.
- `uv run --directory back ruff check .`, `ruff format --check .`, `ty check` — clean.
- `bun run --cwd front typecheck`, `bun run --cwd front lint` — clean.
- Payload and latency figures re-measured against the live seed after the change.

## Change log

- 2026-09-28 — Initial assessment; sparse `values` payload implemented and measured.
