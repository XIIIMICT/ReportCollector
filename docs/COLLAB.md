# Collaboration (real-time editing)

Backend: `back/src/collab/` (`ws.py` — WebSocket, `store.py` — storage, `undo.py` — undo/redo, `events.py` — broadcasting, `journal.py` — action journal, `models.py` — canonical_key, `schemas.py`).
Frontend: `front/src/shared/collab/` (`ws-client.ts` — connection/heartbeat/idle, `use-collab-room.ts` — reactive room state).

## Room

`WS /api/ws/raw-materials?project=raw_materials&section=<section>&reportMonth=<month|empty>&clientId=&sessionId=&ticket=`

- Room: `rm:{project}:{section}:{reportMonth}` — «Цены» without a month, «Движение» per month. Edits and presence of different rooms don't intersect.
- Auth: signed headers `X-ReportCollector-*`; the browser doesn't send them — instead the query parameter `ticket` (one-shot signature). Three rejection reasons: invalid identity (4401), no project access (4403), unexpected Origin (4403) — all logged (`collab.ws_identity_rejected`, `collab.ws_access_rejected`, `collab.ws_origin_rejected`), search the logs by these tags.
- Origin is checked against `BACKEND_CORS_ORIGINS` (see the slash landmine in AGENTS.md).
- Per-connection outbound queue (256) + a dedicated writer task: a slow client doesn't block the room (head-of-line blocking).

## Message protocol

Client → server:
| type | payload | meaning |
|---|---|---|
| `ping` | — | heartbeat; if presence expired (was idle) — the member is **re-added to the room** and everyone gets `presence:update`; reply `pong` |
| `presence` | `selection: {…}` | update my selection in presence |
| `lease:acquire` | `cells: [key…]` | acquire cells (≤500 at once, ≤1000 per connection) |
| `lease:renew` | `cells: [key…]` | renew a lease (only if the owner hasn't changed — Lua script) |
| `lease:release` | `cells: [key…]` | release a lease (only own) |

Server → client:
| type | meaning |
|---|---|
| `hello` | room, self, `members` (present members with selection/assets/role) |
| `presence:update` | member list changed |
| `lease:granted` / `lease:denied` | acquire result (denied — with the current owner) |
| `cell:locked` / `cell:unlocked` | cell lease taken/released (the client maintains `remoteLocks`) |
| `cell:changed` | a cell changed (after a successful REST commit or undo/redo): clients apply it live |
| `pong`, `error` | heartbeat / protocol error |

REST edits publish `cell:changed` into the room via `publish_cell_changed` (`back/src/collab/events.py`) — so edits reach all workers through pub/sub.

## Lease, presence, idle (timings)

| What | TTL | Client |
|---|---|---|
| Cell lease (`lease`) | 20s | renew every 10s |
| Room presence | 25s | heartbeat (`ping`) every 15s |
| Idle detection | 2 min without mouse/keyboard/focus **or** `document.hidden` | leases released immediately (`lease:release`), heartbeat/renew stop; on activity — heartbeat resumes, selection re-sent, server re-joins via `ping` |

Result: walked away (tab open) → after ~2 min leases are released, "online" fades ~25s later. Returned — online again. A "forever" lease is impossible (previously the client renewed endlessly).

Both stores (`ValkeyStore`, `MemoryStore`) implement one interface; the choice depends on `VALKEY_URL` (empty = memory). Lua scripts guarantee owner checks for renew/release.

## What blocks what (lock semantics)

- **`remoteLocks`** — cells **leased** by another user (they're editing): block starting an edit, saving an edit, pasting/cutting a range (such cells are skipped with a «ячейка занята» toast), the paste button.
- **`remoteCellInfo`** — cells from another user's **completed selection** (yellow highlight): block *starting* an edit. Nuance: if I'm already editing a cell (my lease is active) and someone else just selected it on top — my save goes through (lease beats selection).
- **Month fixing** checks `any_active_lease` across all the month's cells → «Фиксация временно недоступна: другой пользователь сейчас редактирует ячейку (имя)».
- **Archive version** — read-only, no presence, the person is "offline".

The client maintains `ownedLeases` (Set) and **must remove keys on release** — otherwise it hits the 1000 limit and leases stop being granted.

## Undo/redo (server-side stack)

- Stack is per user **per browser session and form**: `rc:undo:{user_id}:{session_id}:{section}/{reportMonth}` — «Цены» history doesn't undo «Движение», August doesn't undo September. Survives form switches **within one page load**, but a reload gets a fresh `session_id` and starts with an empty history (stale stacks are never referenced again; the client re-reads `/undo-state` on form change).
- An action is one cell (rowId, field, old/new value, actionId, targetTable, section, reportMonth). Stack limit 500, lookup — last 200.
- **Edit→undo race:** the PATCH that commits a cell edit journals the action asynchronously. A Ctrl+Z fired immediately after Enter can reach `/undo` before the journal push — the server would answer `reason=empty` and the edit would stay. The frontend guards this: `runUndo`/`runRedo` in `raw-materials-workspace.tsx` first await the in-flight commit promise (`pendingCommitRef`), so undo is only sent after the edit landed on the server.
- New edits **clear the redo branch** (Lua `_CLEAR_REDO_SCRIPT`).
- Applying undo/redo goes through the same `apply_cell_value` + `bump_cell_state` (revisions, `lastActionId`) and publishes `cell:changed`.
- Conflicts: if a cell was changed by someone else after the action (`lastActionId` mismatch) or is leased by another — the action is rejected with `reason=conflict/locked`; a second undo press passes `skipActionId` to skip the blocked one.
- **Month fixing clears all undo stacks and cell states** (`clear_all_undo_state`) — the snapshot becomes the single point of reference.

## Deployment and WS diagnostics

- The browser connects to `COLLAB_WS_PUBLIC_URL`; empty → same-origin (`wss://<host>/api/ws/...`), nginx proxies to back.
- Symptoms and causes (all are 403 on the handshake, but the logs differ):
  - `collab.ws_origin_rejected` → Origin not in the CORS list. Classic: a slash in `AUTH_APP_ORIGIN` (the browser never sends a slash in Origin). Fix `.env`, recreate containers (`docker compose --profile prod up -d --force-recreate back front`).
  - `collab.ws_identity_rejected` → broken signature/expired ticket (usually after a rebuild with a different `AUTH_SECRET`).
  - `collab.ws_access_rejected` → the user has no access to the project.
  - `ERR_CONNECTION_REFUSED` → nginx doesn't proxy `/api/ws/` or back isn't listening; `ERR_SSL_PROTOCOL_ERROR` → WS goes to the wrong port (not through nginx TLS).
- Diagnostics: `docker logs rc-back --since 30m 2>&1 | grep "collab.ws"`.

## Tests

`back/tests/test_collab.py` — protocol, leases/presence (MemoryStore, `presence_expires_at` for heartbeat), undo scope per session and form, idle-return via ping. `front/e2e/collab.spec.ts` — two-user e2e (only with a running stack).
