# ReportCollector — remaining engineering work

Updated 2026-10-30. Single backlog replacing the backend/frontend audits and
SCALING_BACKLOG. Completed implementation history remains in Git. This list is
not a production-readiness sign-off; checked code and deployment acceptance are
different milestones. Do not reopen the explicit product deferrals below.

## P0 — release acceptance and deployment

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
  Define a consistent global CPU/RAM budget plus per-kind concurrency; the
  current admission count is global but compared with each caller's own limit.
- [ ] Verify the locked hosted CI run and production image/migration/browser
  gates after push. Align reverse-proxy upload, response and timeout limits with
  application settings. Agree SLOs, availability and RPO/RTO with the operator.

## P1 — backend correctness and bounded work

- [ ] Finish plan preflight as one bounded worker operation. It currently hashes
  the upload, scans assets, then parses again; holds a request DB session during
  parsing and reads existing codes across plans. Scope lookups to the target
  plan/assets, release connections before CPU work, and persist the digest and
  bounded validation result. Validate missing catalog definitions explicitly.
  Cap scanned rows and issue reports with total/truncated counts and matching
  frontend explanations (including duplicates and blank-code rows).
- [ ] Finish preflight cache invalidation for row deletion (deleting a row that
  is not the newest leaves the max-`updated_at` plan token unchanged), then
  measure lookup/cache latency. Catalog and asset changes are now covered by
  their own `count(*) + max(updated_at)` tokens; the plan part is still a max
  over the touched assets and cannot represent every dependency.
- [ ] Measure long transaction/project-gate/row-lock contention on a big import.
  The recompute is now narrowed to the rows the load wrote (and only changed
  rows are updated), and the job reports `parse`/`write`/`recompute` progress;
  finer validation/finalize phases and failure-after-`write` observability remain
  open. This performance work is separate from the deferred concurrent-edit
  conflict policy.
- [ ] Move high-cardinality plan facet search/order/paging into bounded SQL work,
  preserving Unicode casefold, decimal ordering, client-timezone dates,
  NULL/empty buckets and preceding-filter semantics. Current code loads the
  entire grouped result with .all() and sorts it in Python before paging, except
  identification codes, whose search/order/paging already runs in PostgreSQL.
  Add a cardinality guard and revision/filter-scoped caching where beneficial.
- [ ] Measure revision-aware counts/count-on-demand for lazy windows. Counts now
  restrict to the requested plan, combine anchor counting and correctly handle
  sparse positions, but still execute on each window. Do not cache by a timer
  or by a token that costs more than the aggregate itself.
- [ ] Bound visible-row ID lists, filter arrays and serialized job payloads;
  return stable 413/422 errors and record rejections.
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

- [ ] Continue extracting remaining shared grid lifecycle/selection/clipboard/
  remote-overlay orchestration around the existing RevoGrid adapter. Adapter,
  filter, export and focus primitives already exist; preserve the renovation
  grid's distinct source/header/summary behavior.
- [ ] Replace repeated full-row snapshots/facet scans with revisioned/indexed
  structures; replace remote-map JSON fingerprints with stable collaboration
  revisions. Benchmark realistic row counts and 1k/10k remote cell markers.
- [ ] Remove the extra unsearched facet request used for totalWithoutSearch in
  serverFacetSource for remaining columns; identification-code facets already
  return this count with the filtered page.
- [ ] Coalesce header-funnel geometry through one observer/RAF scheduling path.
- [ ] Reconcile acknowledged dictionary edits locally instead of refetching the
  entire dictionary after every write.
- [ ] Restore import job dialog/polling after reload; API job durability is
  implemented, automatic UI recovery is not.
- [ ] Add authenticated cold/warm browser traces, long tasks, heap, scroll FPS
  and a Turbopack-compatible route bundle report; establish regression budgets
  from measured baselines. Keep multi-account Keycloak collaboration and
  browser-only grid contracts in the browser gate.
- [ ] Revisit domain router/journal boilerplate only when stable contracts expose
  a shared seam. Preserve project-local memberships, grants and undo scopes;
  do not replace differing project policies with generic CRUD.
- [ ] **Rename the «Ремонт» plan columns away from `col_NNN`.** The db names are
  positional leftovers from the legacy workbook (`col_002`…`col_218`), NOT stable
  identifiers: inserting «Номер сметы» reused the retired `col_218` name for a
  completely different field and re-based every `position`/`ordinal`/Excel range
  after it. Today the field's `dbName`, its workbook ordinal, its form position
  and its Excel letter are four different numbers held in sync only by a JSON
  transform and a data-catalog comment guard. This is unmaintainable — a rename
  to semantic names (e.g. `estimate_number`, `total_cost_rub`) needs one mapped
  migration per project, a versioned import alias table for the customer's XLSB
  headers, and an update of the frozen formula/excelFormula references. Do this
  before adding any further plan column.

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
- Import conflict detection/staged version finalization preventing concurrent
  edit overwrites: accepted temporary testing compromise. No conflict UI now.
- Further local Argon2/password-fallback work; Keycloak is the normal login path.

## Deferred — implementation brief (moved from §6)

Moved here verbatim from `docs/REPORTCOLLECTOR_IMPLEMENTATION_PROMPT.md` §6,
so the brief itself stays an actionable implementation sequence. These items
are NOT part of the current implementation phases; each needs its own decision,
infrastructure work, or owner gate.

- [ ] **S3-compatible object storage** — replace local import/export artifact
  storage with a durable shared object store; design migration, lifecycle,
  credentials and recovery separately (§3.1 keeps current storage).
- [ ] **Export artifact lifecycle** — retention, cleanup, quota/disk-growth
  handling and behavior above the XLSX format ceiling; needs an owner-approved
  retention/product policy.
- [ ] **Multi-process worker topology** — separate API, import/export/query
  workers and outbox dispatch from the API lifespan; needs coordinated
  deployment/infrastructure changes (§3.9).
- [ ] **Infrastructure and deployment review** — NGINX limits/timeouts, Compose
  networks/ports, health/readiness wiring, Docker TLS trust, deployment topology
  and storage mounts (§3.9).
- [ ] **Monitoring and alerting** — metrics exposure, per-process scraping,
  queue-age/worker/DB metrics, alert design, operational dashboards (§3.9).
- [ ] **Production runbooks and disaster recovery** — backup/restore validation,
  RPO/RTO, failure procedures, disk-full/object-store/Valkey/DB outage response
  (§3.9).
- [ ] **Security/logging review** — PII in authentication/application logs,
  retention, redaction and access policy with the organization.
- [ ] **Load/stress certification** — representative large files/dataset,
  concurrent users, multiple replicas, worker crashes, dependency failures,
  performance percentiles, capacity/SLO definition (§3.5).
- [ ] **Full archive-to-archive plan diff** — currently not connected to a
  user-facing workflow; revisit when product scope requires it (§3.4).
- [ ] **Gate A — import/edit conflict policy** — waiting for the owner to choose
  between deferring imports on active leases, optimistic conflict detection,
  explicit serialization, or another option (§3.6).
- [ ] **Gates B/C — code uniqueness scope and duplicate-workbook policy** —
  waiting for owner answers before changing the preflight query or adding a
  unique constraint.
- [ ] **Final document/API limit policy** — last priority; generous,
  evidence-based ceilings after functional work and owner review (§3.10).
- [ ] **Deep sorted-row pagination (if contract-changing)** — only if
  measurements show OFFSET is material and a cursor would alter the grid/API
  contract.
- [ ] **Count/aggregate cache versioning (if needed)** — after query
  measurement; any data-revision protocol must cover every mutation path
  transactionally.

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
