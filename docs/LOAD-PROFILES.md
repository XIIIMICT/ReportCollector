# Target load profiles (ASSUMPTIONS — no usage telemetry exists)

Status: **proposed targets, not measured service-level objectives**. No
production or staging usage data was supplied to this change. Every number
below is an **ASSUMPTION**. `docs/LOAD-BASELINE.md` records the only measured
numbers, from a deliberately **SCALED-DOWN** run on the disposable CI stand;
it does not validate these targets.

## Why these are assumptions

The repository declares a planning target of 50 concurrent users
(`docs/ARCHITECTURE.md`, «Transactional collaboration») and no observed peak,
edit rate, room distribution or reconnect rate. Application metrics exist
(`back/src/metrics.py`: `ws_connections_active`, `ws_room_members`,
`ws_messages_total`, `ws_events_relayed_total`, `ws_slow_consumers_total`,
`ws_disconnects_total`, `http_requests_total`) but no retained sample was
provided for this task. Until telemetry is exported, treat the profile below
as a hypothesis to test, not a capacity contract.

## Profile A — baseline collaboration day (ALL NUMBERS ASSUMPTION)

| Parameter | Assumed value | Basis / replacement |
|---|---|---|
| Concurrent WebSocket clients | **300** | Product sizing hypothesis; replace by the 95th percentile of `ws_connections_active` over 4 weeks |
| Rooms | **10–50** | One “Движение” room per report month plus price/dictionary rooms; replace by distinct `room_section × reportMonth` seen in `ws_room_members` logs |
| Clients per room | **6–30** | 300 / 10–50; replace by the room-member distribution, not the average |
| Active editors (writing) | **~30% of clients (≈90)** | ASSUMPTION; replace by the share of sessions issuing `PATCH /api/raw-materials/movement` or `/prices` |
| Edit rate per active user | **~1 cell edit/s** sustained, bursts to **5 edits/s for 10 s** | ASSUMPTION; replace by `rate(http_requests_total{route=~"/api/raw-materials/(movement|prices)"})` per user cohort |
| Cells per lease/edit batch (`k`) | **1** typical, **5** burst, hard limit 500 | Code limit is measured (`back/src/collab/ws.py:76`); the 1/5 workload split is ASSUMPTION |
| Presence heartbeat | **5–10 s** | ASSUMPTION profile; the shipped client sends `ping` every **15 s** and keeps presence TTL **25 s** (`docs/COLLAB.md`) — load must test both the assumed faster rate and the shipped rate |
| Lease renew | **10 s**, TTL **20 s** | Measured contract (`docs/COLLAB.md`), not an assumption |
| Undo/redo bursts | **~6 edits/min/user**, in runs of **3–10** undo+redo presses | ASSUMPTION; replace by `http_requests_total` on the undo/redo routes |
| Join/leave churn | **~2 joins/s and ~2 leaves/s** steady, 300 joins in 60 s peak | ASSUMPTION; replace by `rate(ws_connections_active)` deltas / `ws_disconnects_total` |
| Disconnect/reconnect storms | **30% of clients reconnect within 10 s** after a simulated outage | ASSUMPTION; replace by incident-history reconnect spikes |

## Profile B — worst-case single room (ALL NUMBERS ASSUMPTION)

| Parameter | Assumed value | Why it matters |
|---|---|---|
| Clients in one room | **300** | Full presence-map fan-out is O(N) queue puts per ping (`docs/COLLAB.md`), so one hot room is the pathological case |
| Ping interval | **5 s** | At N=300 this is an assumed 60 room-wide snapshots/s; the shipped 15 s client would be 20/s |
| Presence snapshot size | **~300 members × ~300 B** | ASSUMPTION (depends on names/assets/selection); replace from a real `presence:update` frame captured in Loki/proxy telemetry |
| Lease storms | **50 clients acquiring disjoint 5-cell batches in the same second** | Stresses the sequential Valkey path: code estimate is **4k sequential round trips** per k-cell acquire+release (`docs/REMEDIATION-PHASE0-RESEARCH.md`) |

## How to replace assumptions with real telemetry

1. Export 4 weeks of `/metrics` (`docs/ARCHITECTURE.md`, «Monitoring») into
   Prometheus: `ws_connections_active`, `ws_room_members`, `ws_messages_total`,
   `ws_events_relayed_total`, `ws_disconnects_total`, `ws_slow_consumers_total`,
   `http_requests_total` by route, `http_request_duration_seconds`.
2. Derive p50/p95/p99 for connection count, edits/s/user, room size and
   reconnect bursts. Use the **observed maximum**, not the mean, to set the
   target VU count.
3. Confirm the browser's real ping/lease rates from proxy/WS frames or an
   instrumented browser trace; the code defaults (15 s ping / 10 s renew) are the
   floor, not the assumption.
4. Replace each ASSUMPTION row above with the measured value **and its source
   window**, then re-run `load/` and revise `docs/LOAD-BASELINE.md`. Do not
   promote an assumed number to an SLO without that step.

## What the k6 suite does and does not cover

- `load/join-leave-storm.js`, `load/steady-edit.js`, `load/presence-scale.js`,
  `load/undo-burst.js`, `load/reconnect-resync.js` implement the five workload
  shapes above against one raw-materials room by default.
- All scripts use the reserved `.test` identity and the real login/ticket flow
  (`load/README.md`); they never use production data.
- They do **not** emulate browser rendering, revo-grid state, Keycloak SSO, the
  nginx proxy, or multi-worker deployment beyond the CI stand's two workers.
- A run at less than 300 clients is a **SCALED-DOWN baseline**. Extrapolation
  is non-linear because presence fan-out is O(N²) in one room and login is
  Argon2-bound; do not multiply a small-run latency to certify 300 clients.
