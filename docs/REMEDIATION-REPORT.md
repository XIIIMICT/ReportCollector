# Backend remediation — final report: ReportCollector (2026-11-27)

Scope: only the confirmed findings in `backend-remediation-brief`. Review base `577a40c`;
final local `dev` = `455a81a`. Task branches merged `--no-ff`; nothing pushed; no
production data, live migration or cleanup performed.

## Phase 0 — research (`608b408`)
`docs/REMEDIATION-PHASE0-RESEARCH.md`: three AGENTS files, **no ADRs**; ~50 metric names;
CI required-check names `CI required` and `dependency-review-gate`. Full compose render
showed **every non-CI published port on `0.0.0.0`** (CI stack already loopback-only).
Lease keys `rc:lease:{project}:{table}/{row}/{field}`, TTL 20 s, owner JSON; the fencing
token is the **PostgreSQL sequence `collab_action_id_seq` → `lastActionId`**, not a Valkey
token. Presence published the full member map to all N clients. No automated PostgreSQL
backup, no usage telemetry.

## P0-1 — branch protection (applied remotely)
Research contradicted the brief: RC has **no `main`** (default `dev`). Per owner decision,
`main` was created from the then-current remote `dev` (`393a53f`) and protected; the same
was done for DM and EL. Verified via `gh api repos/XIIIMICT/<repo>/branches/main/protection`:
`CI required` sole required check, strict, 1 approval, dismiss stale, admins included,
force pushes/deletions disabled. Residual: **`dev` remains default and unprotected**; PRs
to `dev` do not hit the gate. A live push-rejection test was not run (SSH unavailable);
settings verified via API.

## P0-2 — published ports (`eeb0679`)
Per-port decision: front/back/valkey host publications removed (container network only);
postgres, Keycloak and all monitoring admin ports bound to `127.0.0.1`; only nginx stays
public on `80/443` (allow-listed) because it is the actual browser entry and already
terminates TLS and forwards `/api/ws/` with Upgrade headers. The CI stack stays
loopback-only. New `infra/check_host_binds.py` parses rendered `docker compose config`
and is wired into CI; negative proof: temporarily setting grafana to `0.0.0.0:3000` made
the guard exit 1. Proxy smoke: `https://127.0.0.1:…/api/health` ok, HTTP→308 HTTPS,
Playwright health spec passed through the proxy.

## P1-1 — load baseline (`f496312`)
`docs/LOAD-PROFILES.md` (all numbers marked ASSUMPTION: 300 WS clients, 10–50 rooms,
~1 edit/s/user, 5–10 s presence ping, reconnect storms) and `docs/LOAD-BASELINE.md`
(SCALED-DOWN: 20–200 clients; 300/500 UNMEASURED, N=500 presence FAILED with login 500s).
Measured: join hello p95 529 ms; lease RTT p95 346.64 ms vs REST PATCH p95 596.37 ms;
presence updates/s 491.6/1796.9/6106.3 at N=50/100/200; Valkey ~7 commands/cell/cycle;
PG 21–38 connections, no lock waits after runs; back RSS 1006–1189 MiB. Five native k6
scenarios in `load/`, on-demand `.github/workflows/load-baseline.yaml` (not a PR gate),
9-panel dashboard.

## P1-2 — batched lease EVALSHA (`bf426dc`)
Research **disproved** the brief's token premise: leases use owner equality, while
fencing tokens live in PG (`collab_action_id_seq`/`lastActionId` + monotonic `revision`).
One EVALSHA each for acquire/release over the whole dirty-cell set; per-cell TTL, partial
verdicts, oversized-batch errors and PG token monotonicity preserved; `NOSCRIPT` fallback
via `SCRIPT LOAD`. k6 at 1 VU, p95 ms before→after: k=1 37.29→19.59, k=5 21.04→19.29,
k=20 50.00→22.84. p99 is noisy at 1 VU; N=20 concurrency UNMEASURED. Full pytest:
817 passed/17 skipped (memory), 826/8 (Valkey), 81% combined.

## P1-3 — linear presence fan-out (`c797634`)
Sequenced per-room deltas with an idempotent, out-of-order-safe client merge; reconnect
`hello` full snapshot unchanged. Presence updates/s before→after: N=50 486.97→11.62
(41.9×), N=100 1800.68→28.53 (63.1×), N=200 6399.77→62.30 (102.7×); pong p99 at N=200
428.13→10 ms. N=300/500 and Valkey OPS delta UNMEASURED. Tests: 825/17 (memory), 834/8
(Valkey); targeted Playwright collab 3/3 and smoke+health 4/4 passed after rebuilding the
stale CI front image.

## P1-8 — PostgreSQL backups with verified restore (`8ab3ebe`)
Chose a `pg_dump -Fc` sidecar over WAL-G/pgBackRest: the production DB is external PG14,
no PITR requirement and no server-side WAL access. Assumed RPO ≤24 h (not agreed), daily
02:00Z dump, retention 7, weekly Sunday 03:00Z restore drill into a scratch DB with a real
assertion. Real disposable drill succeeded: dump 2804 bytes, restore + data fidelity
42 rows, scratch dropped; wrong-password path produced exit 1 and `backup_failure` metric.
10 narrow metrics, 5 alerts, 2 dashboard panels and a runbook landed together. **OPEN:**
`BACKUP_OFFHOST_TARGET` is empty by default, so the off-host copy requirement is **not
satisfied**; the metric reports `pg_backup_offhost_configured 0`.

## P1-9 — nightly heavy suite (`455a81a`)
`03:17 UTC` + `workflow_dispatch`, not part of `CI required`; all job names prefixed
`Nightly`. Seven jobs: compose/actionlint, full memory+Valkey pytest matrix, combined 80%
coverage, report-only pip-audit/bun audit + freshness, blocking gitleaks reused unchanged,
production-image + Playwright smoke, and k6 load suite. Artifacts 7 days. No CI→Alertmanager
wiring exists (no webhooks; SMTP is Prometheus-only); gap documented, no secret invented.
`pip-audit` 0 findings, `bun audit` `{}` with three `@revolist/*` trial packages skipped by
the private registry — documented. A scheduled night was not observed (no push).

## P2 triggers — NOT TRIGGERED
| Item | Trigger | Status |
|---|---|---|
| P2-2 Valkey HA / app scale-out | >1 app instance or failover need | not triggered; current stand is single-instance |
| P2-3 OpenTelemetry | cross-service latency debugging need | not triggered; no unresolved cross-service bottleneck |
| P2-4 SLOs / error budgets | baseline + weeks of monitoring | not triggered; no telemetry history |
| Broker T2 (section 7) | multi-instance AND at-least-once delivery required | not fired; at-most-once + resync retained by design |

## Remaining OPEN / owner gates
- `dev` (default branch) protection; off-host backup destination; OIDC browser CI;
  target-host load/soak and 300-client profile; RevoGrid integration suite;
  live `0042`/`0043` migration rollout.
- Alert mail delivery and a real scheduled night cannot be verified without push/hosting.

## Runbooks / docs updated
`docs/LOAD-PROFILES.md`, `docs/LOAD-BASELINE.md`, `docs/LEASE-BATCHING.md`,
`docs/PRESENCE-FANOUT.md`, `docs/RUNBOOKS.md`, `docs/COLLAB.md`, `docs/ARCHITECTURE.md`,
`docs/WORKFLOW.md`, `docs/TODO.md`, AGENTS files. No weakened gates, no new runtime
dependency, no pushes.
