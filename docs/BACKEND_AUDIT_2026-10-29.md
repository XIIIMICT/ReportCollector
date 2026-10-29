# Backend and Data-Plane Audit

**Review date:** 2026-10-29
**Scope:** `back/src`, database migrations, CI/release workflow, Prometheus/Grafana/Loki/Alloy/Alertmanager configuration, and the backend-facing parts of the frontend workflow.
**Status:** Phase A runtime hardening is partially complete; durable import/export jobs remain open for Phase B.
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
- database checkout, invalidation and statement-duration metrics, alongside the
  existing HTTP and collaboration metrics;
- Granian reload ignores the application log directory, preventing reload loops.

Still open in Phase A:

- request/upload/response byte metrics, job/import/export phase metrics,
  event-loop/process/disk saturation metrics and alert rules for these signals;
- query-count coverage for `/me` and measured `EXPLAIN` snapshots for the main
  plan/facet/export queries;
- load-based alert thresholds and production scrape/service discovery.

## Executive verdict

The backend has a sound asynchronous foundation and several careful correctness decisions: FastAPI with async SQLAlchemy/asyncpg, explicit project and row locks, a transactional journal, bounded WebSocket queues, route-template Prometheus labels, streaming XLSX primitives, and structured request logging.

The current design is not yet ready to promise predictable behaviour under large files, large renovation plans, or multiple backend replicas. The main risk is not one slow SQL statement. It is the amount of work performed before a background job starts:

- XLSB import reads the upload and the complete workbook into several Python structures, then performs a full-plan recomputation in the same request transaction.
- Generic exports first materialize all database rows and only then enqueue the writer.
- The old renovation export endpoints still build a complete export read model synchronously.
- Export job state and files are process-local, so a restart or a second replica loses ownership/status visibility.
- A single in-process worker serializes all large exports.
- The plan diff endpoint can return and compare two complete plans in Python.
- Database pool, job queue, cache, upload, and runtime saturation are not visible in metrics, so operators cannot distinguish a slow query, an exhausted pool, a stuck worker, and memory pressure.

These issues can produce high RSS, long transactions and lock waits, request timeouts, partial user-visible failures, or an OOM kill even when the ASGI event loop remains responsive.

The recommended next step is a measured job architecture for imports and exports, followed by production-like load tests. Refactoring domain routers into a universal CRUD layer is not the priority and would increase authorization risk.

## Evidence and verification

The backend suite was run with:

```text
uv run --directory back pytest -q
```

```text
532 passed, 6 skipped
```

The original `httpx`/Starlette warning failure was removed by the locked
`httpx2` test dependency. The suite is now green; the six skips are the
explicit optional real-service cases described by the test configuration.

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

- `back/src/renovation/router.py:217` reads the complete upload with `await file.read()`.
- The same payload is passed through `assets_in_file` (`router.py:220`, `import_plan.py:116-142`) and then `inspect_plan_file` (`router.py:251`, `import_plan.py:216-224`). Both paths call `_read_rows`, which returns `list[list[object]]` for the entire workbook.
- Apply reads the upload again at `router.py:280`.
- `parse_plan_rows` returns `list[ParsedPlanRow]` (`back/src/renovation/import_rows.py:30`), so the request can hold upload bytes, workbook cell rows, parsed rows, ORM lookup structures and pending write batches at once.
- `back/src/renovation/import_apply.py:59` moves parsing to a thread, but that changes event-loop scheduling, not the memory model.

**Impact**

A large XLSB can multiply its compressed size into several large resident structures. Concurrent imports or an export running in the same process can cause GC pressure, swap, or an OOM kill. A thread also does not provide CPU parallelism on a regular CPython build.

**Remediation**

1. Stream the upload to a `SpooledTemporaryFile`/controlled temporary path with a configurable byte limit.
2. Parse once and produce metadata plus a bounded row iterator; do not run a separate full workbook pass for asset discovery.
3. Apply rows in bounded chunks; keep only the current chunk and database lookup maps.
4. Persist a digest and preflight result, not the entire payload in memory. Make retries idempotent by digest and import version.
5. Move the whole operation to a durable worker and return `202 Accepted` with job status. Keep the HTTP request limited to validation, enqueueing, and authorization.

### P0-2. Import recomputes the complete plan inside the request transaction

**Evidence**

`back/src/renovation/import_apply.py:112-113` calls `recompute_plan_rows` after writing the imported batches. `back/src/renovation/service.py:2440-2497` scans every row, computes fields in Python, and sends update batches. The implementation is bounded by batch size, but the operation still runs in the request transaction under the project gate.

**Impact**

A 300k-row plan can hold a long transaction and project lock for minutes. Cell edits, reads that need the same gate, and other imports queue behind it. A client timeout does not necessarily mean that the database work stopped; the user can retry and create duplicate pressure.

**Remediation**

Return an import job immediately. Use a staging/import version with explicit states (`uploaded`, `validated`, `loading`, `recomputing`, `ready`, `failed`). Perform the expensive load and recomputation outside the request lifecycle. Acquire the project gate only for the short finalization/swap step, or use a version pointer so readers see either the old complete plan or the new complete plan. Expose progress, row counts, phase duration, cancellation and a stable error.

### P0-3. Process-local job managers are not durable or replica-safe

**Evidence**

`back/src/common/export_jobs.py:141-146` creates one `asyncio.Queue` and one worker task. Renovation has another manager in `back/src/renovation/export_jobs.py:60-105`. Job registries and active-job limits live in process memory; files are written to a local directory.

**Impact**

A slow export blocks every other large export in that process. Restart loses pending jobs and status. With multiple workers or replicas, a status/download request can land on a process that does not know the job. Per-user limits and cache deduplication are also process-local. A local filesystem is not a shared artifact store.

**Remediation**

Use a durable queue and job store (Valkey plus a worker library or a dedicated worker service) and shared object/file storage. Set bounded worker concurrency from measured CPU/RAM limits. Add per-user/tenant quotas, cancellation, leases/heartbeats, retry policy and stale-job recovery. Keep the current in-process manager only as a development fallback.

### P1-4. Generic “background” table export materializes all rows before enqueueing

**Evidence**

`back/src/common/export_jobs.py:397-451` receives `rows: Sequence[BaseModel]`, uses `len(rows)`, and stores `rows=list(rows)` in the job. Raw-materials and KPPK routes read the full view first (`back/src/raw_materials/router.py:385-437` and `_export_rows` around `495-520`; `back/src/kppk/router.py:982-1028`).

**Impact**

The request still pays the complete database query, Pydantic materialization and list memory cost. The worker only moves workbook rendering away from the request. A threshold based on row count cannot protect against a wide or expensive row shape.

**Remediation**

Pass a serializable query specification to the worker, not row objects. The worker opens its own session, uses a server-side cursor or bounded keyset batches, and records progress. Perform an inexpensive count/metadata query separately and make the count optional when it is expensive.

### P1-5. XLSB inspect performs an asset lookup per code

**Evidence**

`back/src/renovation/router.py:221-230` first loads all assets, then runs a separate `SELECT` for every repair code found in the file. The number of round trips therefore grows with the upload instead of remaining constant.

**Impact**

Files containing many assets make preflight slower and hold a database session while the parser has already spent CPU and memory on the workbook. Under concurrent uploads this amplifies pool pressure. The result is also easy to make inconsistent if the dictionary changes between individual lookups.

**Remediation**

Normalize the codes once and resolve them with one `IN` query (or use the already loaded map). Return a deterministic missing-code set. Include the lookup duration and number of codes in import metrics.

### P1-6. Synchronous renovation export endpoints remain exposed

**Evidence**

`back/src/renovation/router.py:656-690` (`GET/POST /plan/export`) calls `read_plan_export_data`. That path builds the export read model before `StreamingResponse` is returned (`back/src/renovation/service.py:712+`).

**Impact**

The endpoint can allocate a complete wide plan and hold the request open despite the newer job API existing at `router.py:715+`. A user or an old frontend path can bypass the intended protection.

**Remediation**

Deprecate and remove the old endpoints after a compatibility window, or make them dispatch to the same job path. Enforce a maximum synchronous export size at the server boundary; never rely only on frontend routing.

### P1-7. KPPK cost and realization exports render synchronous bytes

**Evidence**

`back/src/kppk/router.py:835-854` reads the full cost view, renders to `BytesIO`, and returns `Response(content=payload)`. `router.py:1031-1074` does the same for realization.

**Impact**

A moderate data growth turns these endpoints into request-time memory and CPU spikes. They have no shared row threshold or job status.

**Remediation**

Route all potentially large KPPK exports through the durable export worker, or apply a server-side threshold based on both rows and estimated bytes. Use streaming writers and query specifications.

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

### P2-17. Observability misses the signals needed to operate the data plane

Current application metrics cover HTTP request count/duration/in-progress and WebSocket connections/rooms/events/slow consumers. The dashboards cover service HTTP, collaboration and logs, but not the following:

- import/export queue depth, active workers, queue wait, duration, failures, retries, bytes and rows;
- import phases (upload, parse, validate, write, recompute, finalize);
- database pool usage, checkout wait, query latency by low-cardinality operation, lock waits, deadlocks and statement timeouts;
- upload/request/response bytes and rejected size limits;
- Valkey latency, reconnects, errors and lease/presence failures;
- cache hits/misses for inspect/read caches;
- process RSS/CPU, event-loop lag and file-system free space;
- WebSocket messages by type, auth-check latency, queue depth and disconnect reasons;
- readiness latency and dependency failure type.

Add these as counters/gauges/histograms with operation names, never raw SQL, user IDs, UUIDs or arbitrary filter text as labels.

### P2-18. Alerting and scrape configuration are not production-complete

Current alerts cover sustained/persistent 5xx, backend scrape failure and slow collaboration consumers. Missing alerts include p95/p99 latency, in-progress saturation, DB pool exhaustion, stuck/failed import or export jobs, memory/CPU/event-loop lag, Valkey/Postgres health, disk exhaustion, and repeated restart loops.

`infra/prometheus/prometheus.yml` scrapes a static `host.docker.internal:8000` target with an `env: local` label. That is acceptable for the local stack and is explicitly documented, but production needs service discovery or deployment-specific targets and a distinct environment label.

Build alerts from load-test baselines rather than guessing thresholds. Keep a recording rule for route p95/p99 and a separate set for worker/job SLOs.

### P2-19. Request logs lack data-plane context

`back/src/main.py:138-195` logs method, raw path, status, duration and request ID. It does not include response size, upload size, logical route name, database timing, job ID, phase, rows or bytes. Background workers do not inherit the request ID automatically.

Add a low-cardinality `operation`, `job_id` (searchable field, not a metric label), phase, row/byte counts and outcome. Propagate a correlation ID into the job record and log it on every phase. Avoid logging file contents, tokens, full filter payloads or personal data. Consider sampling successful high-volume 200 logs once metrics and traceability are sufficient.

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
| Renovation XLSB inspect | `POST /api/renovation/plan/import/inspect` | HTTP; full upload + full workbook parse; cache lookup | repeated parse, memory amplification, asset N+1 | bounded temp file, one parse pass, digest-scoped preflight |
| Renovation XLSB apply | `POST /api/renovation/plan/import/apply` | HTTP; parse in thread, batched writes, full recompute | long transaction/project lock, retry ambiguity | durable import job + staging/version swap |
| Renovation grid window | `GET /api/renovation/plan/rows` | HTTP; window query plus exact counts | repeated `COUNT(*)`, filter cost | revision-aware counts, keyset windows, measured query plans |
| Renovation diff | `GET /api/renovation/plan/diff` | HTTP; two full plans in Python | unbounded memory/response | paged SQL diff or job artifact |
| Renovation export (job path) | `/api/renovation/plan/export/jobs/*` | one in-process worker; streamed cursor | serialized jobs, process-local state | durable worker and shared artifact |
| Renovation legacy export | `GET/POST /api/renovation/plan/export` | synchronous read model then stream | bypasses job guard | deprecate or dispatch to job path |
| Raw/KPPK table export | `/api/*/export` | full row materialization before generic job | request memory/query latency | worker-owned query + batched cursor |
| KPPK cost export | `POST /api/kppk/cost/export` | synchronous bytes | CPU/RSS spike | common job path/size threshold |
| KPPK realization export | `POST /api/kppk/realization/export` | synchronous bytes | CPU/RSS spike | common job path/size threshold |
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

1. Fix the Starlette/httpx2 dependency mismatch and require a green locked CI run.
2. Add request/upload/response sizes, job/import/export lifecycle metrics, DB pool/query/lock metrics, Valkey metrics, RSS/CPU/event-loop lag and disk metrics.
3. Add size limits and explicit timeouts for uploads, statements, locks, pool checkout and job streaming.
4. Add query-count regression coverage for `/me` and EXPLAIN snapshots for plan windows, counts, facets and exports.
5. Add alerts and dashboards for queue backlog, job failures, pool saturation, memory and dependency health.

### Phase B — establish durable import/export boundaries

1. Add a worker-backed job table/queue and shared artifact storage.
2. Convert XLSB inspect/apply to bounded temporary-file parsing and one-pass metadata/row iteration.
3. Move apply and recompute to a staged/versioned import job with atomic finalization.
4. Convert generic, KPPK and renovation legacy exports to query-spec jobs.
5. Add idempotency keys, cancellation, heartbeat, retry classification, progress and restart recovery.

### Phase C — remove unbounded read paths

1. Replace full plan diff with paged SQL comparison or a diff job.
2. Introduce revision-aware counts and bounded facets.
3. Remove synchronous legacy export endpoints after a compatibility period.
4. Add server-side limits for visible-row lists and response sizes.

### Phase D — validate scale and operate it

1. Run the load scenarios in `docs/SCALING_BACKLOG.md`.
2. Tune pool size, worker count, queue concurrency and Postgres indexes from measurements.
3. Decide whether WebSocket state remains single-process or moves to a horizontally scalable design.
4. Document runbooks for stuck jobs, failed migrations, pool exhaustion, Valkey outage, disk pressure and restart during import/export.

## Final assessment

The backend is a strong correctness-oriented foundation, but large-file and large-plan work is still coupled to HTTP process memory and transaction lifetime. The next investment should be a durable, bounded data-plane worker boundary with measurements around it. Once that boundary exists, the existing async routes, lock discipline, streaming writer and observability foundation can support a production deployment much more predictably.
