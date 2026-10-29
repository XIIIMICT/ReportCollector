# Backend and Data-Plane Audit

**Review date:** 2026-10-29
**Scope:** `back/src`, database migrations, CI/release workflow, Prometheus/Grafana/Loki/Alloy/Alertmanager configuration, and the backend-facing parts of the frontend workflow.
**Status:** Phase A is complete as a local baseline. Backend phases 1 and 2 below describe the write and read/export slices of Phase B; neither is fully complete. Renovation XLSB apply and renovation-plan/КППК cost/realization exports have durable jobs and isolated workers. Staged import finalization, remaining table exports and production load calibration remain open. Version comparison/highlighting is explicitly deferred by product decision.
**Repository constraint:** the existing user change in `compose.yaml` was left untouched.

## Dependency/runtime follow-up (2026-10-29)

The runtime dependency migration requested after this audit is now applied:

- `httpx` was replaced by `httpx2` for Starlette's test client and backend tests.
- `redis-py` was replaced by `valkey-py`; the application imports `valkey.asyncio` and `valkey.exceptions` while retaining the standard `redis://` URL scheme accepted by Valkey servers.
- `uvicorn` was replaced by Granian with the ASGI interface. Docker development keeps Granian reload support; CI uses Granian with two workers for the cross-worker Valkey tests.

Python 3.15 is now the locked runtime in the Docker image and CI. The service
does not opt into a free-threaded build: the XLSB parser, plan recomputation and
XLSX writer remain Python-heavy, so `asyncio.to_thread` keeps the event loop
responsive but does not make those phases CPU-parallel. Parallelism still needs
bounded worker processes after load tests establish safe CPU/RAM limits.

This is a runtime/dependency change, not evidence of a measured throughput gain. Granian can reduce server overhead, but database queries, Python parsing, XLSX generation and the process-local job boundaries remain the dominant risks identified below. The migration therefore needs the same load-test comparison before new SLOs are claimed.

## Runtime follow-up: collaboration transport (2026-10-29)

Two production-visible transport faults were fixed after the initial audit:

- `ValkeyStore` now uses a separate pub/sub client with `socket_timeout=None`.
  Ordinary command connections keep a five-second timeout, but a quiet pub/sub
  room is an intentionally blocking read and must not be treated as a failed
  connection. The previous shared timeout caused the relay to raise
  `valkey.exceptions.TimeoutError` every five seconds and close every room
  socket with `1013`.
- The WebSocket lifecycle catches both Starlette disconnect exception types in
  the handshake, receive loop and cleanup. A browser that closes between the
  upgrade and the first receive no longer becomes a Granian application error.

The WebSocket HTTP status `101` visible in browser developer tools is the
successful protocol upgrade, not a failed GET. A subsequent `1013` close is a
server-side temporary-transport failure; it should remain observable through
the collaboration logs and metrics.

The regression suite now keeps a real Valkey subscription idle for more than
the command timeout before publishing an event. This prevents the timeout
regression from returning when connection options are refactored.

The browser client also exposes the close code, reason and `wasClean` flag to a
diagnostic callback. `101 Switching Protocols` remains a successful handshake;
the later close event is the signal to correlate with backend logs.

## Phase A progress (2026-10-29)

Completed in the current change set:

- locked dependency/runtime migration and a green test/lint/typecheck run;
- bounded XLSB upload hashing with a configurable size limit, streaming parser
  entry points, digest-scoped inspect cache and one asset lookup map;
- explicit PostgreSQL pool bounds plus statement and lock timeouts;
- database checkout/hold, pool occupancy, invalidation, statement-duration and
  statement-failure metrics, alongside the existing HTTP and collaboration
  metrics;
- ASGI request/response byte accounting (including streamed bodies), cache
  hit/miss counters, import phase timings, export queue/worker lifecycle
  metrics, process RSS/CPU, event-loop lag and log/export disk gauges;
- early multipart upload body/timeout guards, Valkey operation timing and
  subscription-failure metrics, plus lock-wait phase timing;
- Grafana panels for pool pressure, export queue wait, data-operation phases and
  runtime saturation, plus alerts for pool exhaustion, query timeouts, failed
  exports, event-loop stalls and disk pressure;
- import cancellation cleanup: the XLSB iterator is closed after write failure
  or cancellation, so a parser thread cannot remain blocked on a bounded queue;
- Granian reload ignores the application log directory, preventing reload loops.

Still open for production: load-based alert thresholds, memory/backlog rules,
per-instance scrape discovery and verification of the locked GitHub CI run.
The local baseline does not establish a production SLO. A durable job
store/worker boundary belongs to Phase B; metrics make the process-local queue
visible but do not make it restart-safe. The custom registry is per process:
do not increase Granian workers behind one scrape endpoint until aggregation
or per-process scraping is designed.

The query-count regression for `/me` and the measured query snapshots are now
closed. The snapshot is stored in
`docs/BACKEND_QUERY_PLANS_2026-10-29.json` and can be regenerated with
`back/src/scripts/explain_backend.py`. It captured 17 sanitized plans against
the working 306,141-row plan (window, deep window, text/date facets and export
metadata). Exact window counts took about 53–54 ms, text/date facets 83–94 ms,
and the largest export-metadata aggregate 484 ms. These are individual SQL
execution times on warm local data, not endpoint latency or a production SLO.
The baseline covers unfiltered windows; combined filters and full export
cursor plans remain part of the load scenarios in Phase D.

## Executive verdict

The backend has a sound asynchronous foundation and several careful correctness decisions: FastAPI with async SQLAlchemy/asyncpg, explicit project and row locks, a transactional journal, bounded WebSocket queues, route-template Prometheus labels, streaming XLSX primitives, and structured request logging.

The current design is not yet ready to promise predictable behaviour under large files, large renovation plans, or multiple backend replicas. The main risk is not one slow SQL statement. It is the amount of work performed before a background job starts:

- XLSB apply now runs in an isolated durable worker with bounded batches, but
  still retains lookup maps and recomputes the full plan in one transaction.
  Preflight validation remains an HTTP operation with an in-memory report.
- Generic exports first materialize all database rows and only then enqueue the writer.
- The old renovation export endpoints still build a complete export read model synchronously.
- Renovation plan and КППК cost/realization export jobs now persist a query
  specification in PostgreSQL with fenced attempts; generic dictionary table
  job state remains process-local.
- Generic table exports still share a single in-process writer; durable jobs
  use supervised child processes with database-enforced concurrency bounds.
- The plan diff endpoint can return and compare two complete plans in Python.
- The new baseline metrics make database pool pressure, job queueing, cache
  behaviour, upload/response sizes and runtime saturation visible. They do not
  remove the process-local job boundary or replace load testing and production
  service discovery.

These issues can produce high RSS, long transactions and lock waits, request timeouts, partial user-visible failures, or an OOM kill even when the ASGI event loop remains responsive.

The recommended next step is a measured job architecture for imports and exports, followed by production-like load tests. Refactoring domain routers into a universal CRUD layer is not the priority and would increase authorization risk.

## Evidence and verification

The backend suite was run with:

```text
uv run --directory back pytest -q
```

```text
MemoryStore: 548 passed, 6 skipped (final isolated-schema run).
Valkey: 548 passed, 2 skipped (disposable container; before adding the four
HTTP upload-limit tests, which were verified in the final MemoryStore run).
```

The original `httpx`/Starlette warning failure was removed by the locked
`httpx2` test dependency. Both transport runs are green; their optional skips
are the explicit cases described by the test configuration.

A generic static quality analyzer was also run. It reported known false positives (for example migration SQL and test credentials), so its output was used only as a map of areas to inspect. The findings below are based on source and runtime behaviour, not on those unverified warnings.

## What is already good

- Async FastAPI and SQLAlchemy/asyncpg keep ordinary I/O off the event loop.
- `asyncio.to_thread` is already used for XLSB parsing and XLSX/XML writing. This protects the event loop, although it is not a reliable CPU-parallel execution strategy on a normal GIL build.
- Renovation export generation uses batched reads and a server-side stream, and the XLSX writer uses an unseekable streaming sink to avoid invalid ZIP output.
- Import writes use batches of 200 and project locking. Journal/undo operations use explicit logical locks and deterministic row ordering.
- Valkey lease operations use atomic Lua scripts. WebSocket queues are bounded and slow consumers are disconnected deliberately instead of allowing unbounded memory growth.
- Prometheus labels use route templates rather than raw IDs, avoiding a UUID cardinality explosion.
- Request IDs and structured JSON events are present. Loki retention and compaction are configured.
- The read cache uses a database-derived change token instead of a hand-maintained invalidation list. This is conservative and avoids serving stale data across workers.
- The existing domain seams are appropriate: raw materials, renovation and KPPK have different data models, permissions, snapshots, and lifecycle rules. A generic cross-project CRUD abstraction is not a safe performance fix.

## Priority model

- **P0:** can take the service down, expose inconsistent state, or make a large operation fundamentally unsafe.
- **P1:** high probability of customer-visible latency, queueing, memory or operational failure at expected scale.
- **P2:** important maintainability/operability work that should be completed before a broad production rollout.
- **P3:** useful hardening after baseline measurements.

## Findings

### P0-1. Large XLSB import has multiplicative memory usage

**Evidence**

- The body is now counted by `back/src/common/upload_limits.py` before Starlette's multipart parser and is rejected at the configured file limit plus a bounded multipart allowance.
- The inspect path hashes the upload, scans asset codes, and performs a second streaming workbook pass. The scan no longer calls `_read_rows`, but it still performs more than one XLSB pass.
- Apply uses `iter_plan_row_batches` and holds one parsed batch plus lookup maps, while `parse_plan_rows` remains a compatibility path for small fixtures and non-streaming callers.
- The request now only stages bytes and creates a durable job. A supervised child process parses and writes bounded batches; the transaction still holds the project gate during the full write/recompute phase.

**Impact**

A large XLSB can multiply its compressed size into several large resident structures. Concurrent imports or an export running in the same process can cause GC pressure, swap, or an OOM kill. A thread also does not provide CPU parallelism on a regular CPython build.

**Remediation**

1. **Done:** move the bounded temporary upload and parser into a durable import worker.
2. Parse once and produce metadata plus a bounded row iterator; do not run a separate full workbook pass for asset discovery.
3. Apply rows in bounded chunks; keep only the current chunk and database lookup maps.
4. Persist a digest and preflight result, not the entire payload in memory. Make retries idempotent by digest and import version.
5. **Done:** move the whole operation to a durable worker and return `202 Accepted` with job status. Keep the HTTP request limited to validation, enqueueing, and authorization.
6. Remaining: staged/versioned finalization and conflict handling for concurrent edits.

### P0-2. Import recomputes the complete plan in a long transaction — moved outside HTTP

**Evidence**

`back/src/renovation/import_apply.py` calls `recompute_plan_rows` after writing the imported batches. The operation now runs in an isolated durable worker; it still scans every row and holds the shared project gate for the full transaction. Cell edits share this gate and can contend on row locks; the gate is not an exclusive editing fence.

**Impact**

A 300k-row plan can hold a long transaction and project lock for minutes. Cell edits, reads that need the same gate, and other imports queue behind it. A client timeout does not necessarily mean that the database work stopped; the user can retry and create duplicate pressure.

**Remediation**

**Done:** return a durable import job immediately; perform parsing and recomputation outside the request lifecycle; expose progress, row counts, cancellation and stable errors; commit the result with the plan writes. **Remaining:** staged/versioned finalization with explicit phases (`uploaded`, `validated`, `loading`, `recomputing`, `ready`, `failed`) and a short finalization/swap gate.

### P0-3. Process-local job managers are not durable or replica-safe — plan and KPPK query exports fixed, generic tables open

**Evidence**

The generic manager in `back/src/common/export_jobs.py` still creates one `asyncio.Queue` and one worker task. Renovation plan and КППК cost/realization paths now use `back/src/common/durable_exports.py` and `data_jobs`; their job registry, ownership, limits and leases are database-backed. Artifacts still require a shared `EXPORT_DIR`.

**Impact**

A slow generic export blocks every other generic export in that process. The durable query exports recover stale leases and expose status across replicas, but a local filesystem is not a shared artifact store and generic dictionary exports remain unsafe at replica scale.

**Remediation**

Keep the durable PostgreSQL queue for plan and query-spec exports and move artifacts to shared object storage with a dedicated worker deployment. Convert generic dictionary jobs to the same repository; keep the in-process manager only as a development fallback until that migration is complete.

### P1-4. Generic “background” table export materializes all rows before enqueueing

**Evidence**

`back/src/common/export_jobs.py:397-451` receives `rows: Sequence[BaseModel]`, uses `len(rows)`, and stores `rows=list(rows)` in the job. Raw-materials and KPPK routes read the full view first (`back/src/raw_materials/router.py:385-437` and `_export_rows` around `495-520`; `back/src/kppk/router.py:982-1028`).

**Impact**

The request still pays the complete database query, Pydantic materialization and list memory cost. The worker only moves workbook rendering away from the request. A threshold based on row count cannot protect against a wide or expensive row shape.

**Remediation**

Pass a serializable query specification to the worker, not row objects. The worker opens its own session, uses a server-side cursor or bounded keyset batches, and records progress. Perform an inexpensive count/metadata query separately and make the count optional when it is expensive.

### P1-5. XLSB asset lookup per code — fixed

**Evidence**

The current router loads the asset dictionary once and resolves the normalized
codes from that map. The remaining cost is the workbook asset-discovery pass;
it is still separate from validation and should be folded into the durable
one-pass worker described in P0-1.

**Impact**

Files containing many assets still make preflight slower and hold a database
session while the parser spends CPU on the workbook. Under concurrent uploads
this amplifies pool pressure, although the lookup round trips no longer grow
with the number of asset codes.

**Remediation**

Keep the single lookup map, return a deterministic missing-code set, and fold
asset discovery into the one-pass import worker. Include lookup duration and
number of codes in import metrics when the worker boundary is implemented.

### P1-6. Synchronous renovation export endpoints remain exposed

**Evidence**

`back/src/renovation/router.py:656-690` (`GET/POST /plan/export`) calls `read_plan_export_data`. That path builds the export read model before `StreamingResponse` is returned (`back/src/renovation/service.py:712+`).

**Impact**

The endpoint can allocate a complete wide plan and hold the request open despite the newer job API existing at `router.py:715+`. A user or an old frontend path can bypass the intended protection.

**Remediation**

Deprecate and remove the old endpoints after a compatibility window, or make them dispatch to the same job path. Enforce a maximum synchronous export size at the server boundary; never rely only on frontend routing.

### P1-7. KPPK cost and realization exports render synchronous bytes — fixed for large views

**Evidence**

`back/src/kppk/router.py` now counts an authorized query before reading it. Small views still render inline; larger views persist a serializable query specification and stream bounded batches from the durable worker. The previous synchronous path remains covered by the small-view branch.

**Impact**

A moderate data growth no longer turns these endpoints into request-time memory and CPU spikes. The remaining operational caveat is the shared artifact directory until object storage is deployed.

**Remediation**

Keep the server-side threshold, bounded cursor and query specification. Add object storage and load-calibrated byte/time limits before multi-replica rollout.

### P1-8. Plan diff loads two complete plans and creates an unbounded response

**Evidence**

`back/src/renovation/service.py:1937-1990` loads target and previous rows using `_rows_by_position` (`1993+`) into two dictionaries, compares every field in Python, and returns every changed/new/deleted row.

**Impact**

For a 300k-row plan with a wide schema this can exceed memory and response-size limits. It also makes one GET request compete with imports/exports for CPU and database connections.

**Remediation**

Define pagination and an explicit maximum diff size. Prefer SQL-side comparison keyed by position and streamed pages. For a complete diff, create a job and store the result as a downloadable artifact. Return summary counts separately from detail pages.

### P1-9. Exact plan counts run on every lazy window request

**Evidence**

`back/src/renovation/service.py:1245-1255` computes filtered and whole-plan counts. `read_plan_rows` calls this for each grid window (`around 1342`) and may run another count for `anchorOffset` (`1387-1394`).

**Impact**

Lazy scrolling can repeatedly execute exact `COUNT(*)` operations over a large plan with arbitrary filters. The frontend needs a responsive window, but it does not always need a fresh exact total on every request.

**Remediation**

Instrument query duration and plans first. Then use a plan revision token with short-lived/versioned count summaries, count-on-demand, or approximate totals. Keep exact counts for explicit user actions and avoid a second count when the anchor is absent.

### P1-10. Facet queries can group a high-cardinality column and sort in Python

**Evidence**

`back/src/renovation/service.py:1477-1646` builds grouped facet queries and maps/sorts the complete group result in Python. The 0025 migration adds useful `(plan_id, column)` indexes, but the query still needs a cardinality and response-size guard.

**Impact**

A column with many distinct values can produce a large grouped result, consume memory, and make the filter popover slow. Search/min/max work performed after grouping cannot reduce database work.

**Remediation**

Push search, ordering and `LIMIT` into SQL where semantics allow. Add a maximum facet cardinality and a “too many values” response. Cache facets by plan revision/filter scope with a bounded TTL. Track facet latency and returned-group count per logical column.

### P1-11. Database pool and statement timeouts are implicit

**Evidence**

`back/src/database.py:13-18` creates the async engine with only `pool_pre_ping=True` and the schema search path. There is no configured pool size, overflow, checkout timeout, recycle policy, statement timeout or lock timeout.

**Impact**

Requests can wait indefinitely for a connection or a lock relative to the API's expected latency. Defaults may be wrong when the number of Uvicorn workers or replicas changes, and Postgres can be exhausted before the application reports a useful signal.

**Remediation**

Make `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`, statement timeout and lock timeout configurable. Size the pool against total workers/replicas and Postgres `max_connections`. Instrument checkout wait, in-use/overflow, timeout and transaction duration. Use a read-only/short timeout profile for grid reads and a separate worker profile for imports/exports.

### P1-12. Authentication rate limiting is process-local

**Evidence**

`back/src/main.py:38-132` stores timestamps in `_rate_limit_store` keyed by client IP. It is bounded and swept, but each process has its own store.

**Impact**

Two Uvicorn workers or multiple replicas multiply the effective limit and allow a client to rotate across processes. A login burst can also create needless Argon2 work. The limiter has no metric for rejected requests.

The login route also calls synchronous Argon2 `hash_password`/`verify_password` from the async service. Password hashing is intentionally expensive; doing it on the event-loop thread lets a burst of login attempts delay unrelated requests in that process.

**Remediation**

Move auth rate limiting to Valkey with an atomic fixed/sliding-window operation and TTL. Key by a carefully normalized client identity/IP combination, with trusted proxy configuration. Add `429` counters and alerts. Keep the current in-process limiter only as a fail-safe when Valkey is unavailable, with an explicit degraded-mode metric.

Run Argon2 verification and first-password hashing in a bounded worker executor, and measure auth CPU time separately from database latency. Do not remove the work factor just to improve request latency.

### P1-13. User access reads contain a project-level N+1 query pattern

**Evidence**

`back/src/auth/service.py:524-557` reads user-project links, then calls `read_project_assets` once for each project. The asset query is repeated for every project even though the raw-material asset table is shared.

**Impact**

This is usually small today, but it scales with the number of projects and makes `/me` latency depend on membership count. It also creates unnecessary database round trips on every session bootstrap.

**Remediation**

Load memberships and asset grants in one or two queries, group in Python, and return the same contract. Add a regression test that asserts query count for a multi-project user.

### P1-14. Migration 0025 creates large indexes with blocking builds

**Evidence**

`back/migrations/versions/0025_plan_row_facet_indexes.py` drops and creates several indexes with ordinary `DROP INDEX`/`CREATE INDEX` statements. The migration runner has a general lock timeout, but the index build itself is not concurrent and the migration has no progress/size-specific deployment guard.

**Impact**

On the 300k-row plan, index rebuilds can hold locks and delay writes. A partially applied migration or a deploy during active editing can extend downtime even when the runner eventually fails on its lock timeout.

**Remediation**

For production, schedule concurrent index builds in a non-transactional migration or an explicit maintenance job, with `lock_timeout`, progress visibility and a rollback/runbook. Validate index existence and query plans after the build. Do not blindly convert every migration to `CONCURRENTLY`; Alembic transaction handling must be designed per migration.

### P2-15. CPU offload is not the same as parallelism

The code correctly uses `asyncio.to_thread` for parsing and workbook writing. On a normal GIL-enabled CPython runtime, Python-heavy XLSB parsing and XML assembly still contend for one interpreter and can saturate the process. Threads are useful for event-loop isolation and blocking native I/O, but they are not a bounded CPU worker pool.

Use separate worker processes/containers for parse and export workloads, measure CPU and RSS, and cap concurrency. Do not increase thread count until load tests show that the workload releases the GIL.

### P2-16. Job lifecycle has weak operational semantics

The in-process managers have TTL pruning and a two-minute no-output stream timeout, but no durable heartbeat, cancellation, retry classification, queue wait timestamp, or distinction between worker crash and generator failure. `BaseException` is caught in the worker and converted to a string error, which can hide cancellation/termination context outside the explicit `CancelledError` branch.

A durable worker should record: accepted time, queue wait, start/end, phase, rows, bytes, attempt, worker identity, error class, retryability, and last heartbeat. The API should return a stable job state after restart.

### P2-16a. The outbox dispatcher polls the database at a fixed high rate

`back/src/collab/outbox.py:31-47` runs a dispatch cycle every 50 ms in every application process. Even when there are no events, each cycle opens a session, attempts an advisory lock and queries the outbox. With multiple workers this becomes steady background database traffic and contention with user requests.

Use notification-driven wakeups where available, or back off exponentially when the queue is empty, while retaining a bounded periodic retry. Record outbox depth, dispatch latency, failed publishes and oldest event age. Keep the advisory lock for cross-worker ordering, but avoid acquiring a database connection twenty times per second per worker when idle.

### P2-16b. Collaboration fan-out is linear in local subscribers

`back/src/collab/ws.py:167-186` loops over every socket in a room for every message. This is a reasonable bounded-room design, but a hot room produces `messages × subscribers` queue operations and each writer performs its own access filtering. There are no room-size, message-rate, queue-depth or access-check-latency metrics.

Set explicit room/member and message-size/rate limits, measure relay and writer lag, and load-test the largest expected room. If rooms must become large, move filtering/aggregation to a dedicated collaboration service rather than increasing the WebSocket queue size.

### P2-17. Observability baseline was incomplete before this change

Before the current change, application metrics covered HTTP request
count/duration/in-progress and WebSocket connections/rooms/events/slow
consumers. The dashboards then lacked the following data-plane signals:

- import/export queue depth, active workers, queue wait, duration, failures, retries, bytes and rows;
- import phases (upload, parse, validate, write, recompute, finalize);
- database pool usage, checkout wait, query latency by low-cardinality operation, lock waits, deadlocks and statement timeouts;
- upload/request/response bytes and rejected size limits;
- Valkey latency, reconnects, errors and lease/presence failures;
- cache hits/misses for inspect/read caches;
- process RSS/CPU, event-loop lag and file-system free space;
- WebSocket messages by type, auth-check latency, queue depth and disconnect reasons;
- readiness latency and dependency failure type.

The current change adds the bounded counters/gauges/histograms and dashboard
panels for the listed HTTP, database, import/export, cache, Valkey and runtime
signals. WebSocket room-size/message-rate and writer-lag metrics remain a
separate P2 item because they need a load-tested room model.
Labels use operation names and other low-cardinality values; raw SQL, user IDs,
UUIDs and arbitrary filter text are excluded. Remaining work is calibration
against production-like load and adding deployment-specific scrape discovery.

### P2-18. Alerting and scrape configuration are not production-complete

The local alert set now covers sustained/persistent 5xx, backend scrape failure,
slow collaboration consumers, pool exhaustion, query timeout/deadlock failures,
export failures, event-loop lag, disk pressure, readiness dependency failures
and Valkey subscription failures. It still does not page on workload-specific
p95/p99 latency, in-progress saturation, stuck imports, memory/CPU or restart
loops because those thresholds need a production-like baseline.

`infra/prometheus/prometheus.yml` scrapes a static `host.docker.internal:8000` target with an `env: local` label. That is acceptable for the local stack and is explicitly documented, but production needs service discovery or deployment-specific targets and a distinct environment label.

Build alerts from load-test baselines rather than guessing thresholds. Keep a recording rule for route p95/p99 and a separate set for worker/job SLOs.

### P2-19. Request logs lack data-plane context

The former middleware logged only method, raw path, status, duration and
request ID. The current middleware adds the matched route, request/response
bytes and completion outcome; export workers inherit request ID and log job,
phase, queue wait, rows and bytes. Database timing remains in metrics rather
than request log fields.

Keep metric labels low-cardinality. `job_id`, row counts and byte counts belong
in searchable log fields, never metric labels. Avoid logging file contents,
tokens, full filter payloads or personal data. Consider sampling successful
high-volume 200 logs once metrics and traceability are sufficient.

### P2-20. The audit surface has no production-like load gate

`docs/SCALING_BACKLOG.md` correctly records that load tests are not started. Without HTTP/WS/import/export scenarios at realistic concurrency and data volume, pool sizes, queue limits, alert thresholds and SLOs remain assumptions.

Create repeatable scenarios for:

- grid windows and filters on 94k/306k-row datasets;
- import files at representative compressed and expanded sizes;
- export of full, filtered and visible-row scopes;
- KPPK realization/cost slices;
- WebSocket rooms with concurrent edits and reconnects;
- restart during queued/running jobs.

Collect p50/p95/p99 latency, RSS, CPU, DB pool wait, lock wait, queue wait, job throughput, error rate and dropped WebSocket consumers.

## Endpoint and workload inventory

| Workload | Current path | Current execution | Main risk | Target direction |
|---|---|---|---|---|
| Renovation XLSB inspect | `POST /api/renovation/plan/import/inspect` | HTTP; bounded temporary upload, asset scan + streaming validation, digest cache | repeated parse, report size, HTTP lifetime | one-pass durable preflight |
| Renovation XLSB apply | `POST /api/renovation/plan/import/apply` | `202`; durable upload, isolated child, atomic data/result commit | long transaction/project gate and stale editing leases | staging/versioned finalization with edit conflict handling |
| Renovation grid window | `GET /api/renovation/plan/rows` | HTTP; window query plus exact counts | repeated `COUNT(*)`, filter cost | revision-aware counts, keyset windows, measured query plans |
| Renovation diff | `GET /api/renovation/plan/diff` | HTTP; two full plans in Python | unbounded memory/response | paged SQL diff or job artifact |
| Renovation export (job path) | `/api/renovation/plan/export/jobs/*` | durable PostgreSQL job, fenced child worker, streamed cursor | shared artifact storage and worker deployment | shared storage + load-calibrated concurrency |
| Renovation legacy export | `GET/POST /api/renovation/plan/export` | synchronous read model then stream | bypasses job guard | deprecate or dispatch to job path |
| Raw/KPPK table export | `/api/*/export` | full row materialization before generic job | request memory/query latency | worker-owned query + batched cursor |
| KPPK cost export | `POST /api/kppk/cost/export` | bounded inline response or durable query job with cursor batches | shared storage, load-calibrated concurrency | production worker deployment and load gate |
| KPPK realization export | `POST /api/kppk/realization/export` | bounded inline response or durable query job with cursor batches | shared storage, load-calibrated concurrency | production worker deployment and load gate |
| Collaboration WS | WebSocket routes under `/api` | in-process room map + Valkey fan-out | replica/process boundary, queue churn | measured limits, durable presence/pubsub strategy |
| Auth login | `POST /api/auth/login` | Argon2 in request; process-local limiter | brute-force cost and per-process limits | Valkey limiter, auth metrics and baseline |

## Frontend-facing consequences

The current frontend already uses lazy grid windows, header facets and export-job polling. That is the right interaction model, but it makes backend cost visible quickly: scrolling and changing a filter can generate repeated window/count/facet requests, while a legacy export route can still bypass the job UI. Debouncing in the grid cannot fix exact-count queries, process-local queues or request-time row materialization. The API contract should therefore make expensive work explicit (`202`, stable job ID, progress and error), keep window responses bounded, and expose a revision token so the frontend can reuse counts/facets safely during one plan revision.

## Proposed service targets

These are starting assumptions for load-test planning, not accepted SLOs:

- interactive read/window endpoints: p95 <= 500 ms, p99 <= 1.5 s at the agreed concurrency and dataset size;
- ordinary writes: p95 <= 750 ms, with lock-wait and validation failures reported separately;
- import acceptance: authenticated upload is accepted/enqueued in <= 2 s after size/type validation;
- import processing: a target completion time must be set per file-size class after measuring parse/write/recompute phases;
- export: queue wait p95 <= 10 s, with completion targets per row/byte class;
- no request may hold a project lock for the full duration of a multi-minute import/export;
- availability and RPO/RTO must be agreed with the deployment owner; they cannot be inferred from the current local compose stack.

## Recommended remediation phases

### Phase A — make the current service measurable and safe

1. Fix the Starlette/httpx2 dependency mismatch and require a green locked CI run. **Migration and local gates done 2026-10-29; GitHub CI remains to be verified after push.**
2. Add request/upload/response sizes, job/import/export lifecycle metrics, DB pool/query/lock metrics, Valkey metrics, RSS/CPU/event-loop lag and disk metrics. **Done as a local baseline 2026-10-29.**
3. Add size limits and explicit timeouts for uploads, statements, locks, pool checkout and job streaming. **Done for incoming HTTP body/file bytes and receive timeout, DB, pool and existing stream guards; align proxy limits during deployment.**
4. Add query-count regression coverage for `/me` and EXPLAIN snapshots for plan windows, counts, facets and exports. **Baseline done 2026-10-29:** regression test in `back/tests/test_auth_scenario.py`, unfiltered window/count/facet/export-metadata snapshot in `docs/BACKEND_QUERY_PLANS_2026-10-29.json`, capture script in `back/src/scripts/explain_backend.py`. Combined filters and full export-cursor plans need the Phase D workload matrix.
5. Add alerts and dashboards for queue backlog, job failures, pool saturation, memory and dependency health. **Local dashboards and failure/capacity alerts done 2026-10-29. Memory/backlog alerts and thresholds still need load calibration.**

### Phase B — establish durable import/export boundaries

1. **Initial slice done 2026-10-29:** renovation plan export jobs now persist their query specification, ownership, status, progress, attempt, lease, retry class and artifact key in PostgreSQL (`data_jobs`). A polling worker claims rows with `FOR UPDATE SKIP LOCKED`; stale leases retry with bounded backoff, cancellation fences the writer, and status/download can be served by another backend process. Completed artifacts live under `EXPORT_DIR` (the default is the existing log volume) and are TTL-pruned. XLSX generation runs in a supervised child Python process, so CPU-heavy XML generation does not share the ASGI interpreter. The child reports bounded progress and exits without logging to the rotating application file.
2. Add shared object storage and a dedicated long-lived worker deployment for production replicas. The current default directory is durable only when the deployment mounts the same volume; it is intentionally not presented as S3-compatible storage.
3. **Partially done 2026-10-29:** КППК cost/realization now use query-spec jobs with bounded cursor batches; generic tables and renovation legacy exports still materialize rows in the request or use the legacy in-process manager.
4. **Phase 1 slice done 2026-10-29:** XLSB apply is accepted into `IMPORT_DIR` and returns `202` with a durable `renovation_import` job. A supervised child owns parsing and the transaction; plan writes and terminal job result commit together, while lease loss/cancellation rolls both back. Digest idempotency is scoped to the working plan. The UI polls the job and keeps the completion summary. Input bytes remain on disk until terminal cleanup or TTL.
5. Move apply and recompute to a staged/versioned import job with atomic finalization.
6. Extend idempotency, cancellation, heartbeat, retry classification and progress to imports and the remaining exports. Include isolated-worker DB/RSS/CPU metrics in production scraping.

### Backend Phase 1 — durable write boundaries

**In progress.** Completed: the renovation plan import acceptance boundary described above. The
worker commits the data and result atomically, validates the lease at commit,
and pins the target plan at acceptance. A reply lost after commit cannot reapply
the file. Import CPU work runs in a supervised child process. Regression tests
cover restart, duplicate acceptance, cancellation/expiry rollback, lost replies,
queue rejection cleanup and a real XLSB subprocess failure.
The remaining P0 work is staged/versioned finalization: the current worker still
holds the project lock during batched writes and full recomputation. That lock
is now outside the request lifetime, but it must be shortened further before a
large import is advertised as fully parallel.

### Backend Phase 2 — durable read/export boundaries

**In progress.** Completed: renovation-plan and КППК cost/realization exports persist query
specifications, use PostgreSQL leases, and generate in isolated child
processes. Generic dictionary/table exports still use the legacy process-local
manager and materialize rows in the request; converting those routes requires
query-owned loaders for each domain and is deliberately the next slice. The
frontend's old/new plan comparison and change highlighting remain deferred and
must not be pulled into this backend phase.

The remaining generic export slice must cover snapshot row identity,
movement/price projections, empty access
scopes, selected-row ordering, user dictionaries and large-job downloads with
domain contract tests before replacing the existing routes.

The initial slices were verified with 571 backend tests passing (including
durable-import contract tests; six optional skips), 373 frontend unit tests,
backend lint/format/type checks, frontend lint/type checks, and Keycloak browser
scenarios that enqueue a one-row export and an invalid import, reload the page,
read their persisted statuses through the API and download the completed XLSX.
Repeating the export request returns the same job. These scenarios verify API
durability; automatic restoration of the import dialog/polling after page
reload is still open. The migration roundtrip/catalog tests passed. The local
database is upgraded through `0028_data_job_result`; the user's existing
`compose.yaml` changes are excluded from this work.
The existing toolbar scenario timed out on the cold Next.js route at 30 seconds
and passed on a focused rerun with a 90-second test budget. This does not replace
the Phase D large-export and crash-under-load scenarios.

### Phase C — remove unbounded read paths

1. **Deferred by product decision (2026-10-29):** version comparison and change
   highlighting for renovation plans and other tables are currently unused.
   Do not implement or redesign these paths in the current remediation phases.
   Before re-enabling them, replace full in-memory comparison with paged SQL
   comparison or a bounded diff job and cover the highlighting contract.
2. Introduce revision-aware counts and bounded facets.
3. Remove synchronous legacy export endpoints after a compatibility period.
4. Add server-side limits for visible-row lists and response sizes.

### Phase D — validate scale and operate it

1. Run the load scenarios in `docs/SCALING_BACKLOG.md`.
2. Tune pool size, worker count, queue concurrency and Postgres indexes from measurements.
3. Decide whether WebSocket state remains single-process or moves to a horizontally scalable design.
4. Document runbooks for stuck jobs, failed migrations, pool exhaustion, Valkey outage, disk pressure and restart during import/export.

### Future work — version comparison and change highlighting

Return to old/new version comparison only when the feature is scheduled for
use. Scope includes all affected tables, their diff API, row/cell highlighting,
deleted-row representation and a load-tested bounded comparison strategy.
Existing behaviour is left unchanged by Phase B; this deferral is not a claim
that the existing full-plan diff is suitable for large datasets.

## Final assessment

The backend is a strong correctness-oriented foundation, but large-file and large-plan work is still coupled to HTTP process memory and transaction lifetime. The next investment should be a durable, bounded data-plane worker boundary with measurements around it. Once that boundary exists, the existing async routes, lock discipline, streaming writer and observability foundation can support a production deployment much more predictably.
