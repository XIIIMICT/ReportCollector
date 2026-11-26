# ReportCollector — remaining engineering work

Updated 2026-11-26. Single backlog replacing the backend/frontend audits and
SCALING_BACKLOG. Completed implementation history remains in Git. This list is
not a production-readiness sign-off; checked code and deployment acceptance are
different milestones. Do not reopen the explicit product deferrals below.

## P0 — release acceptance and deployment

- [ ] **P0 FIRST — enforce work/repair-code uniqueness WITHIN ONE PLAN; schema
  rollout still BLOCKED.** Owner decision (2026-11-24): uniqueness scope is one
  plan/version (`r_plans.id`), NOT the whole database. `r_plan_rows` uses the
  semantic column `work_or_repair_code` (migration 0041 renamed `col_003`);
  `col_003` no longer exists and must not appear in new SQL. Code work DONE on
  branch `fix/renovation-work-code-uniqueness`:
  1. [x] Preflight (`router_import.py`) resolves the WORKING plan first, reads
     existing codes only with `plan_id = :working`, and includes the plan id in
     the Valkey report scope — an archived-only code is new in a newer version.
  2. [x] Apply (`import_apply.py`) independently rejects a code repeated in the
     workbook (one set spans all 200-row batches), refuses a code already
     duplicated in the TARGET plan, serializes applies per plan, and keeps the
     set-based `UPDATE ... FROM` writer. A plan-scoped advisory lock plus a
     unique index on the transaction-local staging table stop a second insert.
  3. [x] Migration `0042_plan_work_code_unique` is AUTHORED for
     `UNIQUE INDEX uq_r_plan_rows_plan_work_or_repair_code ON r_plan_rows
     (plan_id, work_or_repair_code)` (metadata matches `plan.py`). **Not executed
     by the author.** Plain `CREATE UNIQUE INDEX` (no `IF NOT EXISTS`) fails on
     existing duplicates; `lock_timeout=30s` makes a blocked build fail loudly.
  4. [ ] **Maintainer gate, still open:** run the read-only
     `uv run --directory back -m src.scripts.audit_plan_work_code_duplicates`
     (plan id, code, row ids, positions, counts), back up `r_plan_rows`, clean
     live duplicates as a reviewed data change, re-run the audit until empty,
     approve a maintenance window and only then run `just migrate`. Procedure:
     docs/DATA-JOBS.md, «Work-code uniqueness rollout».
  Gate C duplicates are part of the same problem: the whole import is still
  rejected when a workbook repeats a code; that behavior stays. Historical rows
  from older plan versions must NOT block a newer import — now enforced in both
  preflight and apply.
- [ ] Establish a repeatable production-like workload matrix: 306k-row plan
  windows/counts/facets with combined filters, KPPK slices, full/filtered exports,
  representative XLSB/XLSX inspect/apply, concurrent collaboration, and worker restart
  during jobs. Record p50/p95/p99, RSS/CPU, pool/lock/queue waits, throughput and
  dropped sockets. The successful full-plan browser download on 2026-10-30 is
  one functional check, not this concurrency baseline. Keep disposable schemas
  and artifacts in a reusable load harness; retain EXPLAIN evidence in
  BACKEND_QUERY_PLANS_2026-10-29.json and extend it to filtered/cursor workloads.
- [ ] Deploy shared artifact storage for all backend replicas (shared
  EXPORT_DIR/IMPORT_DIR or object storage). Verify download/retry after replacing
  the originating worker, retention, cleanup and storage failure behavior.
- [ ] Separate durable job supervisors from API lifespan into a dedicated worker
  service. Retain leases/fencing and prove restart recovery in the deployment.
  Define a consistent global CPU/RAM budget plus per-kind concurrency; admission
  uses `MAX_ACTIVE_JOBS` globally and per-call limits in the repositories.
- [ ] Verify the locked hosted CI run and production image/migration/browser
  gates after push. Align reverse-proxy upload, response and timeout limits with
  application settings. Agree SLOs, availability and RPO/RTO with the operator.

## P1 — backend correctness and bounded work

- [ ] Finish plan preflight as one bounded worker operation: scope the existing-code
  lookup to the target plan/assets and persist the bounded validation result.
  The request DB session is already released before CPU parsing, and issue lists
  are capped at 1000 per category with exact totals (`docs/LIMITS.md`). Validate
  missing catalog definitions explicitly; row/column caps are intentionally not
  introduced (streaming parsers, no evidence for a business maximum).
- [x] Finish preflight cache invalidation for row deletion — DONE (2026-11-26).
  The plan part of the scope is `plan=<id>|rev=<r_plans.revision>`;
  migration 0043 bumps that counter with DB triggers on every work-code-changing
  insert/update/delete (including imports, undo/redo and recompute), so deleting
  a non-latest row re-keys the report. `INSPECT_CACHE_VERSION` moved `7` → `8`.
  Catalog and asset changes keep their own `count(*) + max(updated_at)` tokens.
  Migration 0043 is AUTHORED ONLY (rollout follows the blocked 0042 gate, see
  `docs/DATA-JOBS.md`); measure lookup/cache latency after deployment.
- [ ] Measure long transaction/project-gate/row-lock contention on a big import
  IN PRODUCTION. The recompute is narrowed to the rows the load wrote (and only
  changed rows are updated), and RC-3 added bounded staging, parse-before-transaction
  and phase metrics for parse/staging/gate-wait/gate-hold/apply/recompute/commit/
  finalization; the synthetic before/after numbers are in `docs/DATA-JOBS.md`.
  A real large plan, multi-replica contention and worker-heartbeat interaction
  are still unmeasured. This performance work is separate from the deferred
  concurrent-edit conflict policy.
- [x] **RC-3 — bounded import memory** (2026-11-26). Exact issue/duplicate/new/
  update totals with bounded examples; disk-backed duplicate spool; apply uses
  transaction-local temp tables instead of O(N) Python sets/lists; recompute
  streams written ids in 1000-row pages. Parsing precedes the session, gate and
  transaction in the durable worker. Tests: `back/tests/test_import_bounded_memory.py`
  and the job tests in `test_durable_imports.py`; contract in `docs/LIMITS.md`.
- [x] **High-cardinality facets run in PostgreSQL** (Phase 6.1): generic columns
  are grouped, searched, numeric/casefold-sorted, counted and paginated in one
  SQL scan, preserving null buckets/preceding filters; regression tests cover
  ordering, search, nulls and offsets. Still open: a cardinality guard and
  revision/filter-scoped caching where measurement justifies it.
- [ ] Measure revision-aware counts/count-on-demand for lazy windows. Counts now
  restrict to the requested plan, combine anchor counting and correctly handle
  sparse positions, but still execute on each window. Do not cache by a timer
  or by a token that costs more than the aggregate itself.
- [ ] Bound visible-row ID lists, filter arrays and serialized job payloads;
  return stable 413/422 errors and record rejections. **RC-6 scope note:** export
  `rowIds` is deliberately NOT capped (owner decision) and filter `values` /
  search strings stay uncapped pending owner approval — only the legacy
  full-plan read received a resource guard. See `docs/LIMITS.md`.
- [x] **RC-6 — bound the legacy full-plan read without breaking compatibility**
  (2026-11-26). `GET /plan` keeps the historical full response up to 5000 rows
  (the approved inline-work number, not a product cap); a larger plan gets HTTP
  422 with the fixed code `PLAN_FULL_READ_TOO_LARGE`, `maxRows` and `alternative`
  `/plan/rows`, and no rows are materialized or truncated. Consumer inventory:
  the app reads only paged `/plan/rows`; the `getRenovationPlan` frontend wrapper
  has no in-app callers; backend tests and unknown external clients keep small
  full reads. Tests in `tests/test_plan_read_compat.py`; contract in
  `docs/LIMITS.md`.
- [x] **RC-4 — Decimal arithmetic in the renovation formula engine**
  (2026-11-26). Numeric literals and inputs stay `Decimal` end-to-end; division
  uses 28 significant digits, results are quantized to the declared precision
  with `ROUND_HALF_UP`, negative zero is normalized and non-finite/overflow
  errors are deterministic. All 133 form formulas compile and materialize;
  tests in `tests/test_renovation_decimal_formulas.py`. The legacy `formula.rs`
  is absent from the repo — the tie-rule evidence and decision are in
  `docs/DATA-JOBS.md`, «Renovation formula arithmetic».
- [ ] Complete atomic KPPK multi-cell batch writes/paste; a serialized frontend
  queue alone does not give an all-or-nothing backend transaction. Complete
  per-cell revision/read integration and multi-account proof of stale-edit,
  undo, reconnect and scope-change behavior.
- [ ] Retire compatibility synchronous export routes and the unused in-memory
  manager after confirming no consumers need them. Current application paths
  already gate inline work and dispatch large scopes to durable query jobs.
- [ ] Provide a concurrent large-index upgrade procedure, progress/invalid-index
  checks and rollback guidance. Never rewrite an applied migration.

## P1 — observability and operations

- [ ] Aggregate metrics correctly across API workers and child processes; a
  process-local registry scraped through a load-balanced endpoint is incomplete.
  Export child RSS/CPU/DB/phase data through the supervisor or dedicated scrape.
- [ ] Add queue wait/oldest-job/heartbeat-age and stuck-import alerts, detailed
  import phases, outbox depth/oldest-event/publish-failure/delivery-latency metrics,
  and disk sampling for IMPORT_DIR as well as export/log directories.
- [ ] Add production target discovery, distinct environment labels, route
  p95/p99 recording rules and calibrated saturation, memory/CPU, restart-loop
  alerts. Restrict /metrics to the monitoring network in the deployment.
- [ ] Verify single-error alert delivery to the operator mailbox after deployment
  and monitor notification failures. The immediate HTTP rule is now loaded
  locally; SMTP delivery was last explicitly verified on 2026-10-27.
- [ ] Load-test WS fan-out/reconnect across processes/replicas, choose room/member
  admission limits, and measure inbound authorization latency. Application
  message limits do not prevent ASGI from materializing a frame; configure and
  verify transport/proxy limits too. Current member gauges sum by section, not
  individual room size; keep IDs out of metric labels.
- [ ] Add operational runbooks for stuck jobs, DB pool/locks, Valkey failure,
  disk pressure, API/worker restart and failed migrations. Calibrate file/Loki
  retention and sample successful high-volume logs; retain errors and request IDs.

## P2 — frontend performance and maintainability

- [ ] Remove the now-unused `getRenovationPlan` compatibility wrapper (and its
  `RenovationPlan` type) from `front/src/shared/api/renovation-plan-api.ts` once
  external full-`GET /plan` clients are confirmed absent. The app reads only
  paged windows; the backend now refuses plans above the 5000-row budget with
  `PLAN_FULL_READ_TOO_LARGE` (see `docs/LIMITS.md`). Not done here: frontend
  files are outside the RC-6 change scope.
- [ ] Continue extracting remaining shared grid lifecycle/selection/clipboard/
  remote-overlay orchestration around the existing RevoGrid adapter. Adapter,
  filter, export and focus primitives already exist; preserve the renovation
  grid's distinct source/header/summary behavior.
- [ ] Replace repeated full-row snapshots/facet scans with revisioned/indexed
  structures; replace remote-map JSON fingerprints with stable collaboration
  revisions. Benchmark realistic row counts and 1k/10k remote cell markers.
- [x] **Facet totals come from the same grouped SQL scan** (Phase 6.1): generic
  columns return `totalWithoutSearch`/counts via window functions alongside the
  page; no second unsearched request remains for them.
- [ ] Coalesce header-funnel geometry through one observer/RAF scheduling path.
- [ ] Reconcile acknowledged dictionary edits locally instead of refetching the
  entire dictionary after every write.
- [x] **Import job UI re-attaches after a remount/reload** — `use-plan-import-job.ts`
  keeps the id in sessionStorage and resumes polling (test in
  `__tests__/use-plan-import-job.test.ts`).
- [ ] Add authenticated cold/warm browser traces, long tasks, heap, scroll FPS
  and a Turbopack-compatible route bundle report; establish regression budgets
  from measured baselines. Keep multi-account Keycloak collaboration and
  browser-only grid contracts in the browser gate. **No React DevTools /
  Performance baseline was captured before the 2026-11-26 frontend refactor**,
  so there is no retroactive before/after FPS or latency evidence; do not claim
  such gains for the structural changes. Bundle before/after evidence is in the
  workspace root `FRONTEND-AUDIT-FINAL-REPORT.md`.
- [ ] **Frontend audit §5.2/5.3 — stateful decomposition (deferred).** The pure
  workspace selectors and grid geometry/header-chain/value-normalization
  boundaries are extracted and tested. Data loading, collaboration,
  commit/undo, dialog composition and the imperative RevoGrid
  selection/clipboard/scroll integration remain in `RenovationWorkspace` and
  `RenovationGrid` on purpose: they own memo identity and grid contracts. Any
  further split must keep those identities and be gated by the large-plan e2e
  suite.
- [ ] **Frontend audit §8.2 — animation timing tokens (deferred).** The
  consolidated reduced-motion block is in place; the ~15 ad-hoc durations and
  easings in `front/src/app/styles/*.css` were intentionally not tokenized.
  Consolidate only together with a before/after visual check.
- [ ] Revisit domain router/journal boilerplate only when stable contracts expose
  a shared seam. Preserve project-local memberships, grants and undo scopes;
  do not replace differing project policies with generic CRUD.
- [x] **Rename the «Ремонт» plan columns away from `col_NNN`** — done by migration
  0041 (semantic names, physical form order) + the frontend legacy-key decoder;
  only the synthetic `col_002` asset column remains. See docs/GRID.md,
  «Semantic field names (migration 0041)». Historical §9.1 references in the
  applied migration 0041 refer to the deleted implementation brief; the frozen
  `RENAME` plus `LEGACY_FIELD_NAMES` are now the only source of truth.

## Explicit product deferrals

- «Логистика»: the main «Ставки» table is in place, but «Продукт» and both
  route points are read-only placeholders that stay NULL, and the dollar rate is
  the constant `83.90` until the corporate source provides it. The «%» deviation
  is temporarily the same rub difference as «руб. без НДС» (the division arrives
  with the real formula). Undoing a
  transport-type change restores the transport but not the vehicle type the
  change had cleared (the dependent clear is not journaled). The `dict`
  schema is external and must never be written by this application.
- The «Заблокирован» ROLE drives every project: «Логистика», «Сырьё», «КППК»
  and «Ремонт» edit a membership role and the access bit is derived from it
  (renovation's blocked value lives in `r_project_role`, migration 0038). An
  asset worker without grants is equivalent to blocked everywhere.
- Caching the «Логистика» dictionaries: they are read on every rates request and
  every cell validation, but the tables are tiny (tens of rows) and the five
  reads are cheap; a Valkey cache would add TTL staleness for a few
  milliseconds, and the corporate `dict` is written by another system (no
  invalidation hook). Revisit only if the dictionaries grow or a profile shows
  them in the hot path.
- Old/new version comparison and change highlighting, including the currently
  unbounded full-plan diff. Use bounded SQL pages or a job before re-enabling it.
- Import conflict policy: Gate A (whole-form blocking + server-side 409) is
  implemented and recorded below; still open are stale cell leases and staged
  version finalization during an import — decide whether a product policy is
  needed. See `AGENTS.md` landmine 13a.
- Further local Argon2/password-fallback work; Keycloak is the normal login path.

## Deferred — owner decisions, infrastructure and future work

These items are NOT part of the completed implementation phases. Each needs its
own decision, infrastructure work or owner gate; they are recorded here instead
of in a separate brief.

- [ ] **Keep the modular monolith** — do not split into microservices or adopt a
  new service architecture without explicit owner approval. Separate job workers
  are tracked above and do not change this decision.

- [ ] **S3-compatible object storage** — replace local import/export artifact
  storage with a durable shared object store; design migration, lifecycle,
  credentials and recovery separately (current storage stays local/shared-FS).
- [ ] **Export artifact lifecycle** — retention, cleanup, quota/disk-growth
  handling and behavior above the XLSX format ceiling; needs an owner-approved
  retention/product policy.
- [ ] **Multi-process worker topology** — separate API, import/export/query
  workers and outbox dispatch from the API lifespan; needs coordinated
  deployment/infrastructure changes.
- [ ] **Infrastructure and deployment review** — NGINX limits/timeouts, Compose
  networks/ports, health/readiness wiring, Docker TLS trust, deployment topology
  and storage mounts. **Q9 open item (2026-11-25):** no trusted corporate CA
  bundle is available for the build stages, so `uv sync --allow-insecure-host`
  and `NODE_TLS_REJECT_UNAUTHORIZED=0` stay as documented deviations in both
  ReportCollector and DocMasker; replace them with `NODE_EXTRA_CA_CERTS` /
  system trust store when the CA is provided (host runs already use
  `UV_SYSTEM_CERTS=1` and `NODE_OPTIONS=--use-system-ca`).
- [ ] **Deep sorted pagination** — sorted windows still use SQL
  `OFFSET`: 3.2–3.8 s at a 150k offset on the 261k-row live plan. A covering
  index `(plan_id, lower(col), col, position, id)` measured 0.8–1.4 s and a
  0.11 s head without changing the API/UI; the owner chose to keep current write
  cost. Cursor navigation would change the grid's infinite-scroll + go-to-№
  contract. Measurements in docs/ARCHITECTURE.md.
- [ ] **Stream the Next upload proxy** — the route handler buffers
  multipart into one `Buffer`; re-streaming `request.body` into undici lost the
  parts in the Next dev runtime, and Next 16.4.5 documents only response
  streaming. Options: direct-to-backend upload with signed identity headers,
  proxy termination in the central nginx stack, or a chunked upload API. Do not
  add a fragile forward without verifying the full payload on the real runtime.
- [ ] **Monitoring and alerting** — metrics exposure, per-process scraping,
  queue-age/worker/DB metrics, alert design, operational dashboards.
- [ ] **Production runbooks and disaster recovery** — backup/restore validation,
  RPO/RTO, failure procedures, disk-full/object-store/Valkey/DB outage response.
- [ ] **Security/logging review** — PII in authentication/application logs,
  retention, redaction and access policy with the organization.
- [ ] **Load/stress certification** — representative large files/dataset,
  concurrent users, multiple replicas, worker crashes, dependency failures,
  performance percentiles, capacity/SLO definition.
- [ ] **Full archive-to-archive plan diff** — currently not connected to a
  user-facing workflow; revisit when product scope requires it.
- [x] **Gate A — import/edit conflict policy — IMPLEMENTED (2026-11-25).** While an
  import runs, **the whole «Пообъектный план» form is blocked**. Users on it see
  a RED chip in the form status reading
  «\<ФИО обновляющего\> обновляет базу (\<прогресс\> %): подождите немного,
  прежде чем вернуться к редактированию» (FIO and live progress from the durable
  job). Editing is restored when the job finishes; in-progress input is
  discarded by refetching the grid (saves are instantaneous). The import also
  clears the plan's Undo history in the same transaction, so an old action
  cannot replay over freshly imported values. Backend:
  `GET /plan/import/active` (any member) + server-side 409 guard in
  `update_plan_cells`; frontend: `usePlanImportLock` polling + `FormStatus`
  red chip. The shared advisory gate itself does NOT serialize edits/imports.
  Remaining follow-up: push terminal job state over WebSocket instead of
  polling (optional; poll already authoritative).
- [ ] **Gates B/C — DECIDED (2026-11-24), code DONE, schema rollout BLOCKED.**
  Uniqueness scope = one plan; the DB may still have duplicate codes, so the
  **P0 FIRST** maintainer gate above remains open. Duplicate codes inside one
  workbook keep rejecting the whole import. Current sites: the working-plan
  lookup is in `router_import.py` (`inspect_renovation_plan_import`), the
  apply-side guards are in `import_apply.py` (`_ambiguous_codes`, `apply_plan_file`),
  and the indexes are in `plan.py`:
  `ix_r_plan_rows_plan_work_or_repair_code` (non-unique, live) and
  `uq_r_plan_rows_plan_work_or_repair_code` (unique, authored in migration 0042,
  not applied to live).
- [x] **Gate D — DECIDED (2026-11-24) and DONE.** The owner explicitly accepted
  the table rebuild (261k rows, ~824 MB, ~1.6–2 GB peak disk + WAL, all FKs /
  indexes / comments recreated, maintenance window). Migration 0041 performed
  it; the dev DB was migrated and verified on 2026-11-24. Applied migrations are
  immutable — never rewrite it.
- [x] **Final document/API limit policy — DECIDED (2026-11-25) and recorded in
  `docs/LIMITS.md`**: 1000-per-category issue cap with exact totals, no export
  row-id cap, no cell-char limit, explicit WS lease errors, other values unchanged,
  no parser caps, infra/proxy untouched. Remaining gaps tracked in `LIMITS.md`.
- [ ] **Keycloak/application session policy (owner review)** — the 400-day
  `rc_session` lifetime is a demo configuration, not a production security policy;
  production must use the organization's session limits. The contradictory
  «12 hours» comment in `.env.example` should be corrected without changing auth
  behavior; record the production decision with the owner.
- [ ] **Renovation field type anomalies (owner decision)** — the semantic
  columns from migration 0041 `adjustment_work_volume_unit` (was `col_083`,
  «Ед. изм.») is typed numeric, `subcontract_contract_date` (was `col_198`,
  «Дата») is text, `subcontract_contract_number` (was `col_199`, «Номер») is date.
  Migration 0041 preserved these types deliberately; ask before any type/data
  conversion and document how existing values would be interpreted.
- [ ] **Export inline/background threshold (product decision)** — the switch uses
  row count only (`INLINE_EXPORT_MAX_ROWS = 5000`); wide plans with modest row
  counts may still be expensive. Present options including selected column count
  and estimated cell/value size; agree any latency target with the owner.
- [ ] **Remaining import/lease edge cases after Gate A** — the form block and Undo
  clearing are in place, but stale cell leases and staged version finalization
  during an import remain open; trace them and decide whether a product policy is
  needed (`AGENTS.md` landmine 13a).
- [ ] **Deep sorted-row pagination (if contract-changing)** — only if
  measurements show OFFSET is material and a cursor would alter the grid/API
  contract.
- [ ] **Count/aggregate cache versioning (if needed)** — after query
  measurement; any data-revision protocol must cover every mutation path
  transactionally.

- [ ] **Frontend refactor prompt — remaining stages (not covered).** The
  current audit prompt `frontend-refactor-agent-prompt.md` is only partly
  implemented: Stage A (inventory), Stage B (clipboard I/O), Stage C (neutral
  grid contracts, pure config types/formatters), the DocMasker Stage G items,
  and the first three grid-decomposition slices — shared skipped-cell reasons,
  pure DataGrid edit decisions, the selection-helper naming/accessor contract
  (§3.1/§3.3), all pure `grid-config.ts` slices (types/formatters, descriptors,
  cell rendering — now 360 lines), and the DataGrid scroll-restore hook
  (§4.1 step 4) — are merged into `dev`. **Deferred with evidence, not
  overlooked:** the remaining stateful DataGrid controllers (selection,
  clipboard, header geometry, sorting/filtering), all RenovationGrid stateful
  decomposition (§4.2), `ColumnFilterPopover` (§4.5) and `SmartSelect` (§4.6)
  lifecycles, and workspace decompositions (§4.7). Reason: each remaining
  boundary currently returns or consumes roughly 8–15 interdependent values,
  so extracting it would create exactly the "large hook with dozens of values"
  the prompt forbids; the sparse grid additionally needs a large-plan e2e
  harness that is not available here. These are explicitly **not covered by
  CI**; pure-helper tests are not a substitute. Acceptance when resumed: one
  coherent lifecycle per branch, all RevoGrid invariants from prompt §6
  encoded in tests before edits, no callback identity or memo-dependency
  changes, large-plan e2e after every meaningful stage, and no
  runtime-performance claim without a reproducible measurement. Detailed
  evidence — `FRONTEND-REFACTOR-STAGE-A.md` (workspace root). The full deferred
  browser-integration scope is recorded just below.

### Deferred browser-integration coverage (NOT implemented, NOT in CI)

The isolated CI smoke project exercises local auth on a seeded stand only.
**Neither item below is covered by CI, and pure-helper/unit tests are not a
substitute for either.** The `ui-keycloak` project is not started by CI.

- [ ] **(a) DataGrid / RenovationGrid integration coverage — DEFERRED (full
      scope).** Real-browser coverage of the stateful RevoGrid contracts, to be
      added before any further stateful decomposition is accepted:
      - **Visible vs source index mapping:** every index→row operation (paste
        `models`, cut, copy, presence selection keys, `canPasteRange`,
        shift-select, Ctrl+A) must use `getVisibleRows()` under sorting and
        filtering; assert values land on the visible row, never `rows[position]`.
      - **Sparse InfinityScrollPlugin:** window chunks, preload, eviction and
        stale-response suppression — assert no ghost rows and no late response
        overwriting an evicted chunk.
      - **Row identity:** stable ids across sort/filter/reload, no id reuse
        after eviction, correct id→row resolution for patches.
      - **Unloaded paste rows:** a range spanning unloaded chunks must resolve
        and load those rows before writing; no phantom rows, no silent skips.
      - **Pinned rows:** read-only `::pinned` copies survive eviction and sort
        and stay excluded from clipboard/paste guards.
      - **Scroll restoration:** position/anchor survives edit, reload and
        scope/version switch.
      - **11-level header mapping:** grouped/funnel header cells resolve to the
        correct leaf columns and coordinates in both pinned and scrolling
        viewports.
      - **Lease/edit semantics:** single-cell `data` array vs range patch +
        `models`, busy-cell skip, lease TTL/renew, undo journaling and the
        page-lifetime `session_id`.
      - **Test infrastructure:** prepared full stack (Postgres + Valkey +
        deterministic seed), chromium, workers=1, representative 94k/306k-row
        plan windows, deterministic fixtures, local auth (no Keycloak needed
        for these flows).
      - **Acceptance:** large-plan e2e after every meaningful stage; no callback
        identity or memo-dependency regressions; no weakening of existing
        assertions; no runtime-performance claim without a reproducible
        measurement.
- [ ] **(b) Real Keycloak browser CI path — DEFERRED.** Requires an isolated
      IdP/realm and committed non-production configuration plus a runtime
      secret; no new corporate IdP is introduced by the current work. Scope:
      `KEYCLOAK_ENABLED=true` isolated stack, realm/client setup, credentials
      supplied through CI secrets, and browser assertions on the actual OIDC
      callback — redirect to the realm, callback with state/nonce, session
      cookie establishment, claims→user mapping (email, roles), callback
      failure handling and logout redirect. Acceptance: the `ui-keycloak`
      project runs in CI against the prepared realm without a corporate IdP,
      the callback assertions above pass, and no secret is committed. Not
      covered by CI today.

### Test-quality follow-ups (senior-qa audit, 2026-11-26)

- [x] **Dictionary CRUD e2e fixed.** The monolithic spec was split; the CRUD
  case switches project once via keyboard, asserts `POST /api/raw-materials/assets`
  succeeds, re-opens the section so the created record is a real row, selects
  it by the stable `data-rgrow` in the row-header viewport, and deletes it with
  `try/finally` API cleanup. Verified 3/3 consecutive runs with zero orphan
  assets; no assertion weakened.
- [x] **Invalid-reference paste warning restored.** `clipboard.spec.ts:164`
  failed because earlier flaky CRUD runs had polluted the disposable CI stand:
  orphan «Тест Актив» assets and a deleted `СНХЗ` dictionary entry made the
  grid report `значение «СНХЗ» отсутствует в справочнике` instead. The stand was
  re-migrated/re-seeded; the test passed 2/2 without code changes. Root cause
  was test data, not production behavior.
- [ ] **Reduce arbitrary sleeps and weak selectors.** 22 `waitForTimeout` calls
  and ~16 CSS-targeted semantic controls remain; keep only flicker-sampling
  waits, prefer `getByRole(..., { exact: true })` and web-first assertions.
- [ ] **Fix docs/config drift.** `docs/FRONTEND-REFACTOR.md` test section is
  stale (ports/counts); root `AGENTS.md` names `--project=keycloak` while the
  config declares `ui-keycloak`.

## Process (hard rules)

Short recap — the canonical text is `docs/WORKFLOW.md`; this list is the living
reminder, not a second copy:

- Every meaningful task = its own `feat/`/`fix/`/`docs/` branch from current
  `dev`, created before the first edit. Start the next task only after the
  previous one is merged. Split into semantic parts: one logical change per
  commit, each with Why/What/Verified.
- `feature → dev`: `git merge --no-ff` (keeps granular history) after green
  checks + user confirmation; squash is ONLY for `dev → main` (release approval).
  Delete the local branch after the merge; never delete a remote branch without
  permission.
- When in doubt, ask the maintainer — do not guess. Ambiguous product/config/
  process decisions stop and wait for an answer; safe reversible technical
  choices may proceed with documentation. Record maintainer decisions here or
  in the commit body so the next agent does not re-ask.
- Before reporting done: Definition of done (`docs/WORKFLOW.md`) — checks + CI
  green, behavior verified by hand where tests can't see it, docs updated in the
  same change, no debug artifacts/secrets, every ambiguity asked.
- Keep `AGENTS.md` a thin index; one fact = one home. Update it and the matching
  `docs/` file in the same change; record landmines immediately.

## Closed in the current slice

- Renovation plan column sorting is now enabled for every leaf (server-side
  ordering for the Infinity Scroll scope), with multi-level persistence,
  numbered arrows/status output, stable SQL tie-breakers and visible-export
  propagation. The old audit note that the plan had no sorting is obsolete.

- Renovation export materialization preserves editable work codes and invalidates
  old renderer artifacts. Browser export of all 2,336 Soda-Chlorate rows matched
  the source codes exactly (the previous workbook omitted 855 codes).
- KPPK exports carry the grid's merge-suppression state for active/restored
  filters and sorting; repeated clicks on the active tab preserve export state.
- Development schema upgraded through 0029_data_job_runtime; job metadata
  responses serialize timestamps correctly. Full plan downloaded in Chromium
  through Keycloak, including reload while the job was running.
- Plan scope counts and sparse window anchors corrected; job attempt, worker,
  started/heartbeat time and lifecycle phase persisted.
- WS application message size/rate/UTF-8/JSON handling and bounded queue/member/
  writer/access/disconnect metrics added. No claim of full WS scaling acceptance.
- Single HTTP failure alert added alongside sustained-error rules. Remaining
  monitoring acceptance is listed above; previous audits were consolidated here.
