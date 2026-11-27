# Load baseline — disposable RC CI stand (SCALED-DOWN)

Date: 2026-11-27. Code under test: `eeb0679` (branch `perf/load-baseline`,
clean tree at start). This is a **SCALED-DOWN baseline**, not a certification
of the assumed 300-client profile in `docs/LOAD-PROFILES.md`. No usage telemetry
exists, so those targets remain ASSUMPTIONS. Every number below is tagged
**MEASURED** (taken from a command output), **DERIVED** (arithmetic on measured
numbers or code paths) or **UNMEASURED** / **FAILED**.

## Environment (MEASURED)

| Item | Value |
|---|---|
| Host | Windows host, PowerShell; `k6 version` → `k6.exe v2.4.0 (commit/dd9395906d, go1.26.11, windows/amd64)` |
| Docker | Server `29.8.2` |
| Stand | `reportcollector-ci` compose project: `back` `127.0.0.1:19000`, `front` `127.0.0.1:16000`, postgres `127.0.0.1:16432`, valkey `127.0.0.1:17379` (override `rc-ci-ports.yaml`, not committed) |
| Images/config | `rc-back:ci` with 2 Granian workers, `rc-front:ci`, `postgres:18-alpine`, `valkey/valkey:8-alpine`, `KEYCLOAK_ENABLED=false`, `VALKEY_URL=redis://valkey:6379/0`, `AUTH_RATE_LIMIT_MAX=1000` |
| Data | Disposable `rc_ci` / schema `rc`, 24 users (incl. the four reserved `user1..user4@test.local`), 134 `rm_material_movements` rows, 67 in report month `2026-08-01` |
| Identity | reserved `user1@test.local`; password `LoadBaseline2026` was set on the **disposable stand only** through the documented `POST /api/auth/users/{id}/password` flow with a CI-only signed identity, then used via `E2E_TEST_PASSWORD` and the real front login/ticket flow |
| WS endpoint | `ws://127.0.0.1:19000/api/ws/raw-materials?project=raw_materials&section=materialMovement&reportMonth=2026-08-01&clientId=…&sessionId=…&ticket=…` (from `back/src/collab/ws.py`, `docs/COLLAB.md`) |
| Stand health after runs | `/health` 200, `/ready` 200, `pg_locks WHERE NOT GRANTED` = 0, no Lock wait events |

Production data, the dev stands (5000/8000/8080/5432/6379) and the EL/DM stands
were untouched. The stand's reserved-user password change is confined to this
disposable CI database.

## Exact commands

Common environment (PowerShell):

```powershell
$env:E2E_TEST_PASSWORD = 'LoadBaseline2026'
$env:RC_FRONT_URL = 'http://127.0.0.1:16000'
$env:RC_WS_URL = 'ws://127.0.0.1:19000/api/ws/raw-materials'
$env:RC_REPORT_MONTH = '2026-08-01'
```

Smoke (MEASURED — exact output below):

```powershell
$env:RC_VUS='2'; $env:RC_DURATION='5s'; $env:RC_HOLD_MS='300'
k6 run load/join-leave-storm.js
```

Measured scenarios:

```powershell
# (a) join/leave storm, N=50
$env:RC_VUS='50'; $env:RC_DURATION='30s'; $env:RC_HOLD_MS='300'
k6 run load/join-leave-storm.js

# (b) steady editing, N=20, k=1
$env:RC_VUS='20'; $env:RC_DURATION='30s'; $env:RC_CELLS='1'; $env:RC_ROWS='67'
k6 run load/steady-edit.js

# Valkey command-count isolation, k=1 and k=5, 1 VU
$env:RC_VUS='1'; $env:RC_DURATION='8s'; $env:RC_ROWS='67'
$env:RC_CELLS='1'; k6 run load/steady-edit.js
$env:RC_CELLS='5'; k6 run load/steady-edit.js

# (c) presence at N=50/100/200 (all in ONE room, 5 s heartbeat)
$env:RC_DURATION='20s'; $env:RC_PING_EVERY_MS='5000'; $env:RC_SOCKET_MS='20000'
foreach ($n in @(50,100,200)) { $env:RC_VUS="$n"; k6 run load/presence-scale.js }

# (d) undo bursts, N=10
$env:RC_VUS='10'; $env:RC_DURATION='30s'; $env:RC_CELLS='1'; $env:RC_ROWS='67'
k6 run load/undo-burst.js

# (e) disconnect/reconnect resync, N=20
$env:RC_VUS='20'; $env:RC_DURATION='30s'; $env:RC_HOLD_MS='500'
k6 run load/reconnect-resync.js
```

Low-overhead Valkey sampling (no `MONITOR`; `INFO stats` deltas):

```powershell
$b=[int]((docker exec reportcollector-ci-valkey-1 valkey-cli info stats |
  Select-String 'total_commands_processed:(\d+)').Matches[0].Groups[1].Value)
# ... run k6 ...
$a=[int]((docker exec reportcollector-ci-valkey-1 valkey-cli info stats |
  Select-String 'total_commands_processed:(\d+)').Matches[0].Groups[1].Value)
"delta=$($a-$b) ops/s=$(($a-$b)/<elapsed-seconds>)"
```

PG and container sampling (2 s background sampler):

```powershell
docker exec reportcollector-ci-postgres-1 psql -U postgres -d rc_ci -Atc \
  "select count(*) from pg_stat_activity where datname='rc_ci'; select count(*) from pg_locks where not granted"
docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}'
```

## Smoke — exact k6 output (MEASURED, abridged only in the progress lines)

```
     execution: local
        script: load/join-leave-storm.js
     scenarios: 1 scenario, 2 max VUs, 35s max duration
              * joinLeave: 2 looping VUs for 5s (exec: joinLeave, gracefulStop: 30s)

    checks_total.......: 52      10.32653/s
    checks_succeeded...: 100.00% 52 out of 52
    checks_failed......: 0.00%   0 out of 52

    ✓ hello names the room
    ✓ WS handshake is 101

    http_req_duration..............: avg=11.3ms   min=1.68ms   med=2.36ms   p(95)=83.4ms   p(99)=125.96ms max=125.96ms
    http_req_failed................: 0.00%  0 out of 28
    http_reqs......................: 28     5.560439/s
    iteration_duration.............: avg=386.94ms min=362.89ms med=370.01ms p(95)=525.17ms p(99)=583.65ms max=587.14ms
    ws_connecting..................: avg=11.64ms  min=6.43ms   med=8.48ms   p(95)=33.82ms  p(99)=41.85ms  max=42.55ms
    ws_handshakes_ok...............: 26     5.163265/s
    ws_hello_rtt_ms................: avg=23.15ms  min=10ms     med=16ms     p(95)=71.5ms   p(99)=95.75ms  max=99ms
    ws_msgs_received...............: 76     15.092621/s
    ws_session_duration............: avg=323.56ms min=310.48ms med=317ms    p(95)=371.92ms  p(99)=396.05ms max=399.31ms
    ws_sessions....................: 26     5.163265/s

running (05.0s), 0/2 VUs, 26 complete and 0 interrupted iterations
joinLeave ✓ [ 100% ] 2 VUs  5s
```

## Message RTT and throughput (MEASURED)

| Scenario | p50 | p95 | p99 | Throughput / counts |
|---|---|---|---|---|
| Join storm N=50, 30 s — `hello` RTT | 233 ms | 529 ms | 2.67 s | 2141 sessions (69.7/s); handshake connecting p95 322.5 ms, p99 2.2 s |
| Join storm N=50, 20 s (OPS run) — `hello` RTT | 250 ms | 482 ms | 632.15 ms | 1523 sessions (74.0/s); 3044/3045 checks passed |
| Steady edit N=20, 30 s — lease-granted RTT | 138.5 ms | 346.64 ms | 455.81 ms | 610 edit cycles (19.8/s), 0 failed |
| Steady edit N=20, 30 s — REST PATCH | 315.26 ms | 596.37 ms | 744.1 ms | same 610 cycles |
| Presence N=50 — `pong` RTT | 21 ms | 45.09 ms | 174.72 ms | 10 736 presence updates (491.6/s); 150 pongs |
| Presence N=100 — `pong` RTT | 26 ms | 47.05 ms | 80.22 ms | 42 736 presence updates (1796.9/s); 300 pongs |
| Presence N=200 — `pong` RTT | 58 ms | 94.04 ms | 301.02 ms | 169 219 presence updates (6106.3/s); 600 pongs |
| Presence N=200 (OPS run) — `pong` RTT | 43 ms | 122.14 ms | 200.04 ms | 170 672 presence updates (6218.1/s) |
| Undo bursts N=10 — PATCH / undo / redo | 155.82 / 149.05 / 149.2 ms | 372.55 / 331.47 / 287.86 ms | 664.6 / 588.17 / 496.38 ms | 355 cycles (11.6/s), 0 failed |
| Reconnect N=20 — resync `hello` RTT | 115 ms | 336.59 ms | 520.79 ms | 925 reconnects (29.8/s), 1850/1850 checks passed |

`presence_updates_received` is an aggregate across every client in the room:
each heartbeat publishes the full member map to all N sockets, so the count
grows roughly quadratically (50→100: 3.66×, 100→200: 3.47×; DERIVED).
Counting a single client's update rate would be ≈ N / ping-period; the k6
counter deliberately records the total fan-out the stand must deliver.

## Valkey command cost per k-cell edit (MEASURED + DERIVED)

| Run | Cycles | `total_commands_processed` delta | Commands/cycle (DERIVED) |
|---|---:|---:|---:|
| `k=1`, 1 VU, 8 s | 58 | 1644 | 28.3 |
| `k=5`, 1 VU, 8 s | 41 | 2318 | 56.5 |

Marginal cost beyond the fixed per-cycle overhead: **(56.5 − 28.3) / 4 ≈ 7.0
Valkey commands per additional cell per cycle** (DERIVED). The code path
accounts for **4k sequential Valkey round trips** per acquire+release (SET NX
+ PUBLISH, EVAL + PUBLISH — `docs/REMEDIATION-PHASE0-RESEARCH.md`); the measured
marginal count includes the extra REST-side `MGET` and room fan-out, so it is
not a contradiction. These are command counts, not a bounded latency claim.

## Valkey OPS/s (MEASURED, INFO stats delta)

| Scenario | Delta | Window | OPS/s (DERIVED) |
|---|---:|---:|---:|
| Join storm N=50, 20 s | 23 046 commands | 21.2 s | 1086.9 |
| Steady edit N=20, 30 s (sampled run) | 14 476 commands | 30.5 s | ≈474.6 |
| Presence N=200, 20 s | 10 104 commands | 28.12 s | 359.3 |

`MONITOR` was deliberately not used: it would add observable overhead and
change the thing being measured. Each sample itself issues a couple of INFO
commands; the OPS/s figures include that small sampler cost.

## PostgreSQL and process resources (MEASURED, 2 s sampling)

| Metric | Result |
|---|---|
| PG connections (`pg_stat_activity` for `rc_ci`) | min **21**, max **38** across 39 samples during the sampled N=20 steady-edit run |
| Non-granted locks / Lock wait events | 1 non-granted lock in one 2 s sample; **0** Lock wait events and 0 non-granted locks after the runs. No blocked statement observed |
| `rc-back` CPU | avg **61.6%**, max **496.9%** (≈5 cores of the 2-worker Granian process) |
| `rc-back` RSS | avg **1006.5 MiB**, max **1188.9 MiB** |
| `rc-front` CPU / RSS | avg **10.2%** / **141.3 MiB** (max 167.2 MiB) |
| `rc-postgres` CPU / RSS | avg **22.5%** / **345.1 MiB** (max 402.4 MiB) |
| `rc-valkey` CPU / RSS | avg **2.0%** / **5.1 MiB** (max 9 MiB) |

CPU percentages are Docker's cumulative-percentage view (a multi-core backend
can exceed 100%); samples were taken every 2 s during the run, not continuously.

## Failed / unmeasurable scenarios

- **Full 300-client profile: NOT EXECUTED.** The stand and a single Windows k6
  process could not sustain 300 simultaneous logins/heartbeats. Attempted
  N=500 presence run: **FAILED** — 12 script errors `local login failed: HTTP
  500` during the initial burst, `checks_succeeded` 97.53% (475/487), pong p50
  85 ms / p95 2.52 s / p99 4.1 s, 5228 presence updates/s. These figures are
  **not** a baseline and must not be extrapolated. The stand recovered
  (`/health` 200, `/ready` 200, 0 lock waits) after the run.
- **N=300 presence:** UNMEASURED — not attempted after the N=500 failure; the
  same login-burst risk applies.
- **Valkey OPS/s for undo bursts and reconnect storms:** UNMEASURED (no isolated
  before/after INFO delta was captured for those two runs).
- **Latency outside the instrumented paths** (REST reads of `/movement/meta`,
  browser rendering, nginx TLS path, Keycloak SSO, multi-replica fan-out,
  dropped sockets behind a load balancer): UNMEASURED.
- **One join-storm OPS run** had 1 failed check out of 3045 (a `hello` frame
  arriving after the client had already closed). No assertion was weakened;
  the run is reported as-is.

## Caveats and interpretation

1. **SCALED-DOWN baseline.** The largest clean run was 200 clients in one room
   for presence and 50 for churn. The assumed 300-client / 10–50-room profile
   is untested. Do not extrapolate linearly: presence fan-out grows ~N² in a
   single room and login is Argon2-bound.
2. All scripts use one raw-materials room and `materialMovement` cells. A real
   profile spreads clients over 10–50 rooms; per-room fan-out would be lower,
   aggregate Valkey/DB traffic higher. Both remain ASSUMPTIONS.
3. k6 ran on the Windows host, not inside the stand's network. Local loopback
   RTT is therefore near-zero, but the host CPU competed with Docker Desktop;
   absolute latencies are stand-specific and not SLOs.
4. Two backend workers and one front container ran on a shared desktop. CPU/RSS
   figures are not a capacity plan for the production topology.
5. The baseline measures protocol paths, not browser behaviour. No CI gate was
   added: `.github/workflows/load-baseline.yaml` is `workflow_dispatch` only.
6. The Grafana `load-baseline` dashboard (`infra/grafana/provisioning/dashboards-json/load-baseline.json`)
   uses only existing metric names — no new Prometheus metric was introduced by
   this change. k6 trends live in the k6 summary/artifact, not in Prometheus.
