# k6 load suite (native WebSocket)

Five scripts exercise the collaboration protocol against a **disposable**
ReportCollector stand. They use only k6's built-in `k6/http`, `k6/ws` and
`k6/metrics` modules — no npm/bun runtime dependency and no application change.

| Script | Workload |
|---|---|
| `join-leave-storm.js` | connect → `hello` → close, repeated |
| `steady-edit.js` | `lease:acquire` → `PATCH /api/raw-materials/movement` → `lease:release` |
| `presence-scale.js` | N sockets in one room, `ping` on an interval, count `presence:update` fan-out |
| `undo-burst.js` | acquire → PATCH → undo → redo → release |
| `reconnect-resync.js` | close → fresh ticket → reconnect with the same `sessionId` |

## Pointing the suite at a stand

All configuration is via environment variables. Defaults match the isolated
CI stand used for the recorded baseline (`docs/LOAD-BASELINE.md`).

| Variable | Default | Meaning |
|---|---|---|
| `E2E_TEST_PASSWORD` | **required** | Password of the reserved `user1@test.local` … `user4@test.local` fixtures (never a production credential) |
| `RC_FRONT_URL` | `http://127.0.0.1:16000` | Front origin; used for login, ticket and REST PATCH |
| `RC_WS_URL` | `ws://127.0.0.1:19000/api/ws/raw-materials` | Backend WS endpoint; discovered in `back/src/collab/ws.py` and `docs/COLLAB.md` |
| `RC_TEST_EMAIL` | `user1@test.local` | Reserved e2e identity (admin in all projects on the isolated stand) |
| `RC_PROJECT` | `raw_materials` | Must match the endpoint's project |
| `RC_ROOM_SECTION` | `materialMovement` | Room section (`docs/COLLAB.md`) |
| `RC_REPORT_MONTH` | `2026-08-01` | Must be an open period with seeded rows |
| `RC_VUS` | per script | Concurrent virtual users |
| `RC_DURATION` | per script | k6 constant-VU duration |
| `RC_CELLS` | `1` | Cells per lease/PATCH batch (`k`) |
| `RC_ROWS` | `8` | Seeded movement rows made available to VUs |
| `RC_PING_EVERY_MS` | `5000` | Presence heartbeat interval in `presence-scale.js` |
| `RC_SOCKET_MS` | `20000` | Socket lifetime in `presence-scale.js` |
| `RC_HOLD_MS` | `300` / `500` | Hold time before close in churn scripts |
| `RC_SOCKET_TIMEOUT_MS` | `20000` | Per-cycle guard in edit scripts |

The standard CI stand uses different ports (`15000`/`18000`); override both
URLs when running there:

```powershell
$env:E2E_TEST_PASSWORD = '<isolated-stand-password>'
$env:RC_FRONT_URL = 'http://127.0.0.1:15000'
$env:RC_WS_URL = 'ws://127.0.0.1:18000/api/ws/raw-materials'
k6 run load/steady-edit.js
```

## Authentication flow (discovered, not guessed)

1. `POST {RC_FRONT_URL}/api/auth/login` with form fields `email` and
   `password`; the 303 response sets the `rc_session` cookie.
2. `GET {RC_FRONT_URL}/api/collab/ticket?project=raw_materials` with that
   cookie; the front route packs the signed identity headers into a 60-second
   base64url ticket (`front/src/app/api/collab/ticket/route.ts`).
3. Connect to `{RC_WS_URL}?project=…&section=…&reportMonth=…&clientId=…&sessionId=…&ticket=…`.
   The backend verifies the ticket (`back/src/collab/ws.py`), so no custom
   headers are needed and no `Origin` is sent.
4. REST edits go through the front proxy with the same cookie; row ids come
   from `GET /api/raw-materials/movement?report_month=…`, never from a
   fabricated key.

The reserved accounts and password contract are documented in
`front/AGENTS.md`, `front/e2e/helpers/test-users.ts` and
`back/src/scripts/seed_database.py`. `E2E_TEST_PASSWORD` must be supplied by
the operator; there is no password literal in this repository.

## Safety

Run only against a disposable stand (CI stand, local isolated DB). The edit
scripts write to seeded «Движение» cells; they do not touch production data,
Keycloak accounts or corporate emails. Never point `RC_FRONT_URL` / `RC_WS_URL`
at `https://m2v-form-1.h2-group.ru`.

## Validation

`k6 version` must report v2.4.0 in the recorded baseline. The on-demand
workflow `.github/workflows/load-baseline.yaml` uses the pinned
`grafana/k6:2.4.0` container image and is **not** a required per-PR check.
