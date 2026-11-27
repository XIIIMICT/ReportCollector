# Phase 0 research — read-only remediation brief

Scope: `D:\Work\ReportCollector`, branch `dev`, clean tree. No code, Git or remote
changes were made. `file:line` numbers are citations, not measurements; every
quantity claim is tagged **MEASURED** (read from a file/command output),
**ESTIMATE** (derived, not timed) or **UNKNOWN**.

Commands run (all read-only):
`git status --short`; `git branch --show-current`; `git rev-parse HEAD`;
`git remote -v`; `git symbolic-ref --short refs/remotes/origin/HEAD`;
`git ls-remote --symref origin HEAD` (failed: no credentials);
`docker compose [-f <file> | --profile prod --profile dev] config`;
`rg` over `back/src`, `front/src`, `docs`, `infra`, `.github`.

## 1. Declared contracts

- AGENTS files: root `AGENTS.md`, `back/AGENTS.md`, `front/AGENTS.md` [MEASURED].
  No `docs/adr/`, no `ADR.md`, no "ADR" text anywhere in tracked files
  [MEASURED — `rg` + `git ls-files`]. DocMasker (sibling repo) does have `docs/adr/`;
  ReportCollector has none.
- Ports: `FRONT_PORT=5000`, `BACKEND_PORT=8000` from root `.env`, mandatory
  (`AGENTS.md:20`, `compose.yaml:41,67,120,148`) [MEASURED]. Proxy `80`/`443`
  (`infra/compose.proxy.yaml:34-35`) [MEASURED]. Isolated CI host ports
  `127.0.0.1:15000/18000/15432/16379` (`infra/compose.ci.yaml:23,35,49,71`)
  [MEASURED].
- Metrics: `back/src/metrics.py` declares `http_requests_total`,
  `http_last_failure_timestamp_seconds`, `http_request_duration_seconds`,
  `http_requests_in_progress`, `http_request_upload_bytes`, `http_response_bytes`,
  `ws_connections_active`, `ws_rooms_active`, `ws_events_relayed_total`,
  `ws_slow_consumers_total`, `ws_room_members`, `ws_queue_depth`, `ws_messages_total`,
  `ws_disconnects_total`, `ws_access_check_duration_seconds`, `ws_writer_lag_seconds`,
  `db_connections_in_use`, `db_connection_checkouts_total`,
  `db_connection_invalidations_total`, `db_queries_total`, `db_query_duration_seconds`,
  `db_connection_hold_seconds`, `db_query_errors_total`, `db_pool_size`,
  `db_pool_overflow`, `export_jobs_active`, `export_jobs_queue_depth`,
  `export_jobs_completed_total`, `export_jobs_failed_total`, `export_jobs_rejected_total`,
  `export_job_oldest_seconds`, `export_job_queue_wait_seconds`,
  `export_job_duration_seconds`, `export_job_rows`, `export_job_bytes`,
  `cache_hits_total`, `cache_misses_total`, `runtime_event_loop_lag_seconds`,
  `runtime_disk_free_bytes`, `runtime_disk_size_bytes`, `db_pool_acquire_seconds`,
  `db_pool_capacity`, `db_query_failures_total`, `http_response_failures_total`,
  `dependency_check_duration_seconds`, `data_operation_duration_seconds`,
  `import_upload_bytes`, `import_rejections_total`, `valkey_operation_duration_seconds`,
  `valkey_subscription_failures_total`, `auth_rate_limit_events_total`
  (`back/src/metrics.py:57-399`) [MEASURED]. `/metrics` is unauthenticated and
  aggregate-only (`docs/ARCHITECTURE.md:217`) [MEASURED]; scrape job
  `reportcollector-back` at `15 s` interval (`docs/ARCHITECTURE.md:57,223`)
  [MEASURED].
- CI required-check names: `CI required` (`.github/workflows/ci.yaml:340-350`)
  and `dependency-review-gate` (`.github/workflows/dependency-review.yaml:27-39`)
  [MEASURED]. CodeQL `analyze` is conditional on repository visibility
  (`.github/workflows/codeql.yaml:26-52`) [MEASURED]. Whether these are configured
  as branch-protection required checks in GitHub settings is **UNKNOWN**
  (`docs/WORKFLOW.md:212` says to configure `CI required`; no API write/read was made).
- Gates: Gate A — import blocks the plan form via `GET /plan/import/active` + `409`
  guard (`back/AGENTS.md:79`, `docs/COLLAB.md:153`) [MEASURED]; G2 port scheme —
  shared `3000/3100/9090/9091/9093`, Alloy `12345` (`docs/ARCHITECTURE.md:222`)
  [MEASURED]; coverage `80%` combined backend statement/branch
  (`.github/workflows/ci.yaml:136-140`) [MEASURED]; smoke-spec discovery guard
  over `10` spec basenames (`ci.yaml:167-181`) [MEASURED]; hook policy: static
  checkers run on staged files at commit and whole tree in CI
  (`AGENTS.md:88-90`) [MEASURED].
- Deprecation policy: no dedicated policy document [MEASURED]. Declared
  exceptions only: `root_path` deprecated / `openapi_prefix` removed but unused
  (`docs/WORKFLOW.md:132`); legacy DB adoption at `0015_plan_row_col_218`
  unsupported after migration `0041` (`AGENTS.md:40`, `docs/WORKFLOW.md:240`)
  [MEASURED].

## 2. Port / bind inventory

`docker compose config` and `docker compose --profile prod --profile dev config`
on root `compose.yaml` (which `include:`s all four `infra/compose.*.yaml`) rendered
successfully [MEASURED]. Rendering any infra file alone fails with
`service "…" refers to undefined network net` — networks `net`/`net-dev` are
defined only in root `compose.yaml:202-206` [MEASURED]. No rendered port entry
has a `host_ip:` field ⇒ every published port in the non-CI stacks is bound
host-wide (`0.0.0.0` / `::`), not loopback [MEASURED from rendered config;
runtime interface binding not inspected].

| Service | Published → container | Host interface | Genuine consumer |
|---|---|---|---|
| nginx | `80:80`, `443:443` | all interfaces | end-user browser; the only intended public entry point |
| front / front-dev | `5000:5000` | all interfaces | browser in dev; in prod should be reached through nginx |
| back / back-dev | `8000:8000` | all interfaces | WS/REST browser in dev; internal service name on `net` in prod (`compose.yaml:132`) |
| postgres | `5432:5432` | all interfaces | container→container via service name `postgres`; host admin only for `just migrate`/seed |
| valkey | `6379:6379` | all interfaces | container→container via `valkey` in dev (`compose.yaml:91`); host admin only otherwise |
| keycloak | `8080:8080` | all interfaces | browser/OIDC + admin console |
| alloy | `12345:12345` | all interfaces | host admin; command also hardcodes `--server.http.listen-addr=0.0.0.0:12345` (`infra/compose.monitoring.yaml:21`) |
| loki | `3100:3100` | all interfaces | host admin / central stack |
| grafana | `3000:3000` | all interfaces | host admin |
| prometheus | `9090:9090` | all interfaces | host admin |
| alertmanager | `9093:9093` | all interfaces | host admin |
| smtp-relay | none | — | container-to-container only (`infra/compose.monitoring.yaml:100-113`) |
| CI: postgres / valkey / back / front | `15432`, `16379`, `18000`, `15000` | `127.0.0.1` explicitly (`host_ip` present in rendered config) | isolated CI only |

Reverse proxy present: `infra/nginx/default.conf.template` — `:80` → `308 https`
(lines `16-20`); TLS on `:443` (`22-28`); per-request Docker DNS
`resolver 127.0.0.11` (`14`); `set $app_upstream` / `$ws_upstream` (`33-34`);
`/api/ws/` → backend with Upgrade headers and `proxy_read_timeout 3600s`
(`65-76`); `/` → front with `86400s` timeouts (`78-93`); `/health` returns `200`
(`59-63`). TLS is self-signed fallback or host certs
(`infra/nginx/entrypoint.sh:35`, `05-prepare-tls.sh`) [MEASURED]. No auth is
configured in this template; production auth/TLS is expected from the central
DWH stack (`AGENTS.md:23`) — actual remote configuration **UNKNOWN**.

## 3. Lease round-trip and fencing

- Key format `rc:lease:{project}:{table}/{row}/{field}` (`back/src/collab/store.py:36-38`)
  [MEASURED]. Lease TTL `20 s` (`store.py:29`), renewal `10 s`/presence TTL `25 s`/
  heartbeat `15 s` (`back/AGENTS.md:63-66`, `docs/COLLAB.md:76-78`) [MEASURED].
- Owner value is a JSON dict (`userId`, `name`, `email`, `sessionId`, `clientId`,
  `assets`, `role`, optional `block`) (`back/src/collab/ws.py:452-464`).
  **There is no monotonic fencing token in the Valkey lease value**: acquisition
  is `SET NX EX` (`store.py:212-218`), renew/release are Lua owner equality
  checks (`store.py:80-93,220-228`) [MEASURED].
- The fencing token is generated in PostgreSQL: sequence
  `collab_action_id_seq` (`back/src/collab/models.py:12`), consumed by
  `JournalStore.next_action_id()` (`back/src/collab/persistence.py:53-54`), stored
  as `lastActionId` together with an incrementing `revision` in
  `collab_cell_states` (`persistence.py:118-138,144-164`) [MEASURED].
- PG write-path validation: shared project advisory gate + ordered row locks
  (`back/src/collab/locking.py:14-39`, `back/src/raw_materials/service.py:392-394`);
  `check_edit_leases()` does one `MGET` over all `lease_key`s and rejects an owner
  with a different `userId` or `sessionId` (`service.py:581-591`), repeated inside
  journaling (`back/src/collab/journal.py:61-65`); undo compares
  `lastActionId` **and** `lastUserId` and rechecks the lease
  (`back/src/collab/undo.py:339-371`) [MEASURED].
- Sequential Valkey RTTs for a `k`-cell edit (code path measured, counts derived):
  - acquire: `k` × `SET NX EX` (`ws.py:710-712`, `store.py:214`) + one `GET` per
    denied cell (`store.py:217`) + one `PUBLISH` per granted cell
    (`ws.py:726-727`) ⇒ `2k` RTTs whether all are granted or all denied [ESTIMATE].
  - release: `k` × `EVAL` (`ws.py:733-737`, `store.py:226-228`) + one `PUBLISH`
    per released cell (`ws.py:738-739`) ⇒ `2k` RTTs [ESTIMATE].
  - **Total: `4k` sequential Valkey RTTs** for acquire + release [ESTIMATE].
    Limits: `500` cells per lease message, `1000` owned leases per socket
    (`ws.py:76-77`) [MEASURED] ⇒ worst case `2000` sequential RTTs [ESTIMATE].
    No `MULTI`/pipeline is used for lease acquisition/release [MEASURED].
    Disconnect cleanup adds `room_leave` + `room_members` + `publish`
    (`ws.py:756-758`) = `3` more RTTs [ESTIMATE]. The REST write path itself
    touches Valkey once (`MGET` for all cells, `store.py:242-245`), not per cell
    [MEASURED]. No network timing was performed [UNKNOWN].
- Multi-cell atomicity today: one PATCH batch = one journal action, committed in
  one PostgreSQL transaction with business rows, revisions, undo stack and
  outbox (`docs/COLLAB.md:100,144`, `back/src/collab/journal.py:45-105`,
  `back/src/collab/persistence.py:1`) [MEASURED]. Undo/redo applies the whole
  batch or nothing (`back/src/collab/undo.py:230-236`) [MEASURED]. However lease
  acquisition is **per-cell and non-atomic** (loop above); partial grants are
  reported as `lease:granted`/`lease:denied` (`ws.py:718-725`) [MEASURED]. Valkey
  is never authoritative for edits (`persistence.py:1`) [MEASURED].

## 4. Presence broadcast pattern

- Every `ping` refreshes the member, reads the **full** room member map and
  publishes that entire map to the room, then replies `pong`
  (`back/src/collab/ws.py:670-681`) [MEASURED]. The same full snapshot is sent on
  connect (`ws.py:483-487`), on `presence` (`ws.py:690-692`) and on disconnect
  (`ws.py:756-758`) [MEASURED]. Per-member expiry is enforced by Lua
  (`store.py:125-171`) [MEASURED].
- The relay puts each published snapshot into **every** socket queue in the room
  (`ws.py:191-211`); a full queue disconnects that client (`1013`) [MEASURED].
  Cost is `O(N)` queue puts per ping for `N` room connections [ESTIMATE].
- Client applies a **replace**, not a diff: `toMembers()` converts the map to an
  array and `onMembers(members)` replaces state
  (`front/src/shared/collab/ws-client.ts:110-115,255-275`) [MEASURED]. UI maps are
  rebuilt each tick but content-stabilized via a `JSON.stringify` fingerprint so
  the grid is not rebuilt (`front/src/shared/collab/use-remote-cell-maps.ts:18-37,46-114`)
  [MEASURED].

## 5. PostgreSQL backup state

- Local stand: `postgres:18-alpine` (`infra/compose.db.yaml:14`), named volume
  `rc-postgres-data` (`compose.db.yaml:23-24,48-50`) [MEASURED]. No backup
  container, cron, `pg_dump` job or script exists in the repo (`rg` over all
  tracked files: only a manual `pg_dump` instruction in
  `docs/DATA-JOBS.md:516`) [MEASURED]. Automated backup: **none in this repo**
  [MEASURED]. Retention for DB backups: **UNKNOWN**. RPO/RTO: **UNKNOWN** —
  listed as open work in `docs/TODO.md:68,308-309` [MEASURED].
- CI stand: `postgres:18-alpine`, no data volume, loopback `15432`
  (`infra/compose.ci.yaml:21-27,23`) [MEASURED]; disposable by design
  (`ci.yaml:336-338` `down --volumes`) [MEASURED].
- Non-DB retention only: Loki `168h` and Prometheus `15d`
  (`infra/compose.monitoring.yaml:46,64`, `docs/ARCHITECTURE.md:209`) [MEASURED].
  Production PostgreSQL is expected in a separate central stack
  (`docs/ARCHITECTURE.md:57`); its backup/retention is **UNKNOWN** from here.

## 6. Repo state

Branch `dev`; tree clean (no `git status --short` output); dev head
`ab3d641378ac34ce5ba4f1808b86eb467cfd1dc7` [MEASURED]. Remotes:
`origin git@github.com:XIIIMICT/ReportCollector.git` (fetch/push) [MEASURED].
Default branch: **UNKNOWN** — `refs/remotes/origin/HEAD` is unset and
`git ls-remote --symref origin HEAD` failed (`Permission denied (publickey)`);
`main` appears as release/protected target (`AGENTS.md:72`, `ci.yaml:5-7`) but is
not verified as the remote default.

## 7. DocMasker bind-guard adaptation

`D:\Work\DocMasker\infra\check-monitoring-binds.py` (read-only) is a regex scanner
over `compose.yaml` + `infra/compose.{db,monitoring,ci}.yaml`, files `18-23`; it
flags any published mapping whose host part is not `127.0.0.1`/`::1`
(`_bad_mapping`, lines `30-35`) except services in `PUBLIC_SERVICES = {"front"}`
(line `24`); service detection `25`, ports parsing `26-27`, scan `38-69`, exit
code `1` on violations `72-81` [MEASURED].

ReportCollector could adapt it [ESTIMATE], but not verbatim:
1. `FILES` must add `infra/compose.proxy.yaml` and `infra/compose.auth.yaml`
   [MEASURED gap from section 2].
2. `PUBLIC_SERVICES` must include `front`, `front-dev`, `nginx` (80/443) and
   `keycloak`; dev `back`/`back-dev` also need a deliberate exception if direct
   `ws://127.0.0.1:8000` remains supported (`compose.yaml:181`) [ESTIMATE].
3. The regex ignores Compose interpolation and network-scoped declarations; a
   guard built on rendered `docker compose config` would catch env-expanded
   bindings more reliably [ESTIMATE].
4. Current RC CI file is already compliant (all four host IPs are `127.0.0.1`)
   while the non-CI stacks would fail on many services [MEASURED from rendered
   configs].

## Bottom line

The highest-signal findings for remediation are: host-wide publication of every
non-CI port [MEASURED], `4k` sequential Valkey RTTs per `k`-cell lease cycle
[ESTIMATE], full-presence fan-out on every heartbeat [MEASURED], no automated DB
backup or declared RPO [MEASURED/UNKNOWN], and a reusable loopback-bind guard in
the sibling repo [MEASURED].
