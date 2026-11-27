# Presence fan-out — coalesced deltas (P1-3)

Date: 2026-11-27. Branch: `perf/presence-linear-fanout`. This is a
**SCALED-DOWN before/after measurement** on the disposable CI stand, not a
capacity certification. Every figure below is tagged **MEASURED** (copied from
a command output), **DERIVED** (arithmetic on measured figures) or
**UNMEASURED**.

## What changed

- A join, a `presence` selection update or a disconnect now stages one
  `userId -> member | None` change in `RoomManager` instead of publishing a
  full member map. Changes are coalesced for a 1 s window (last write per
  user wins) and published as one `presence:update` frame with a `changes`
  array. A disconnect is a tombstone (`member: null`) and is published only
  when the store confirms the closing `clientId` owned the entry.
- A bare heartbeat publishes nothing. At most one authoritative full `members`
  snapshot per room per 15 s is published, and only when a ping arrives; it
  remains the repair path for dropped frames and silent TTL expirations. The
  reconnect `hello` is unchanged: it still carries the full member map.
- Every presence frame (delta or full) carries a per-room `seq`, assigned in
  one Valkey Lua call as `max(last + 1, wall-clock ms)` with a 300 s TTL (the
  MemoryStore uses the same rule under its lock). Clients apply only strictly
  newer frames; a new `hello` replaces the local view and resets the sequence.
- Per-recipient access filtering now covers delta members too: an asset worker
  cannot learn a foreign selection through a delta, and asset-worker members
  with no overlapping grant stay hidden in both frame shapes.

Consequence (DERIVED): the number of published frames per join burst goes from
O(N) full maps to O(1) coalesced frames; heartbeats go from one full map per
ping to zero frames plus one repair snapshot per ≤15 s. Aggregate bytes for one
burst still contain one N-member map per recipient, so this is **not** a claim
of sub-quadratic bytes on a single burst — only of linear frame and work
growth. The measured fan-out rate now roughly doubles when N doubles.

## Environment (MEASURED)

Same disposable stand and data as `docs/LOAD-BASELINE.md`: compose project
`reportcollector-ci`, `back` at `127.0.0.1:19000` (2 Granian workers),
`front` at `127.0.0.1:16000`, postgres `16432`, valkey `17379`,
`KEYCLOAK_ENABLED=false`, reserved `user1@test.local` identity with the
stand-only password used by the k6 suite. `k6 version` → `k6.exe v2.4.0
(commit/dd9395906d, go1.26.11, windows/amd64)`.

- BEFORE artifacts: `presence-before-{50,100,200}.txt` captured earlier in this
  task from the baseline code (`eeb0679`, `docs/LOAD-BASELINE.md`); they were
  reused, not re-run. The BEFORE rows below are copied from those files.
- AFTER: the `rc-back:ci` image was rebuilt from the working tree and only the
  disposable CI `back` container was recreated; `/health` and `/ready` were
  200 and no presence flush / relay / writer failures appeared in its logs.
  The `rc-front:ci` image was rebuilt later, only for the browser checks below;
  the k6 scripts use the login/ticket HTTP routes, not the browser client.
- Production, dev stands (5000/8000/8080/5432/6379) and DocMasker were not
  touched.

## Exact commands (MEASURED)

PowerShell, from the repository root. Common environment (identical for the
captured BEFORE and the AFTER runs):

```powershell
$env:E2E_TEST_PASSWORD = 'LoadBaseline2026'   # disposable CI stand only
$env:RC_FRONT_URL = 'http://127.0.0.1:16000'
$env:RC_WS_URL = 'ws://127.0.0.1:19000/api/ws/raw-materials'
$env:RC_REPORT_MONTH = '2026-08-01'
$env:RC_DURATION = '20s'
$env:RC_PING_EVERY_MS = '5000'
$env:RC_SOCKET_MS = '20000'
```

Each AFTER run (the captured BEFORE files came from the same commands with the
old image; the baseline recorded them as a `foreach` over 50/100/200):

```powershell
$env:RC_VUS='50';  k6 run load/presence-scale.js *> presence-after-50.txt
$env:RC_VUS='100'; k6 run load/presence-scale.js *> presence-after-100.txt
$env:RC_VUS='200'; k6 run load/presence-scale.js *> presence-after-200.txt
```

The AFTER outputs were retained under
`C:\Users\Scorpi-ON\AppData\Local\Temp\opencode\presence-after-{50,100,200}.txt`.
Each run ended with `checks_succeeded 100.00%`, `http_req_failed 0.00%` and
`presence ✓ [ 100% ] N VUs 20s`.

## Results (MEASURED)

`presence_updates_received` is the aggregate across every client in the room;
`ws_pong_rtt_ms` percentiles are labelled `med`/`p(95)`/`p(99)` by k6 (`med` is
p50). These are stand-specific observations, not SLOs.

| N (one room) | Presence updates/s before → after | Ratio (DERIVED) | Pong p50 before → after (ms) | Pong p95 before → after (ms) | Pong p99 before → after (ms) | WS messages/s before → after | Data received before → after |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 486.97 → 11.62 | 41.9× | 19 → 2 | 29.54 → 3 | 32 → 4 | 496.12 → 20.96 | 6.0 MB → 339 kB |
| 100 | 1800.68 → 28.53 | 63.1× | 21 → 2 | 40 → 4 | 547.32 → 5 | 1817.47 → 46.16 | 24 MB → 764 kB |
| 200 | 6399.77 → 62.30 | 102.7× | 44 → 2 | 70 → 5 | 428.13 → 10 | 6430.07 → 94.37 | 92 MB → 1.7 MB |

Count scaling (DERIVED from the measured `presence_updates_received` rates):
50→100 was 3.70× before and 2.45× after; 100→200 was 3.55× before and 2.18×
after. The remaining roughly linear growth is expected: a burst of N joins
still delivers one N-member frame to each of N sockets.

The earlier baseline's N=200 pong p99 values (428.13 ms / 200 ms on a second
run) were noisy; the AFTER p99 remained ≤10 ms in all three runs. No assertion
or client timeout was changed to obtain these numbers.

## Functional verification (MEASURED)

- Backend: full pytest in both collab backends against the disposable CI
  postgres/valkey. Memory store: 825 passed, 17 skipped, 81% coverage. Valkey
  store: 834 passed, 8 skipped, 81% coverage (with `-W ignore::ResourceWarning`
  for a Windows Proactor deallocator warning from the third-party valkey
  client; no test assertion was changed). `tests/test_presence_fanout.py`
  checks coalescing, tombstone collapse, the sequencing of both stores and the
  unchanged reconnect `hello`, without sleeps as evidence.
- Frontend: `bun run --cwd front test` — 735 pass, 0 fail; typecheck clean.
- Playwright on the rebuilt CI images
  (`E2E_BASE_URL=http://127.0.0.1:16000`, `KEYCLOAK_ENABLED=false`):
  `e2e/collab.spec.ts` — 3 passed (asset-scoped presence, remote lease,
  one-account/two-tab selection takeover); `e2e/smoke.spec.ts` +
  `e2e/health.spec.ts` — 4 passed. The first collab run failed against the
  stale pre-change front image, which ignored `changes` frames; after
  rebuilding that image the same assertions passed unchanged.

## Failed / unmeasurable

- **N=300 and N=500 AFTER: UNMEASURED** — not attempted; the baseline's N=500
  login burst failure (`docs/LOAD-BASELINE.md`) still applies and no attempt
  was made to certify it.
- **Valkey INFO OPS/s before/after for this change: UNMEASURED.** No isolated
  command-count delta was captured, so no OPS claim is made.
- **Multi-replica, nginx/TLS, browser rendering and production telemetry:
  UNMEASURED.** The two-worker stand and loopback k6 host are the same as the
  earlier baseline; see its caveats.

## Interpretation

The protocol path no longer floods every socket with a full member map on
every join and every heartbeat. Latency and fan-out improved substantially at
all three measured sizes, but `docs/LOAD-PROFILES.md` remains ASSUMPTION-only:
no retained production telemetry was supplied, and this run does not certify
300 clients, distributed rooms or a browser-level experience.
