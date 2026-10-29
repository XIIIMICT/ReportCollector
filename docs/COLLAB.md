# Collaboration (real-time editing)

Backend: `back/src/collab/` (`ws.py` — WebSocket, `store.py` — storage, `undo.py` — undo/redo, `events.py` — broadcasting, `journal.py` — action journal, `models.py` — canonical_key, `schemas.py`).
Frontend: `front/src/shared/collab/` (`ws-client.ts` — connection/heartbeat/idle, `use-collab-room.ts` — reactive room state).

## Section policy and UI gates

`back/src/common/section_policy.py` and `front/src/shared/grid/section-policy.ts`
declare static room/history/export/row-authoring/loading modes. Backend tests
compare every section and mode across the two files, including KPPK users.
Workspaces use `collaborationAvailableFor`, `undoAvailableFor` and row-operation
policy; grid Ctrl+A uses `chunked`. These are capability gates, not substitutes
for live server authorization, row/asset/column rules or lifecycle checks.
Immutable archive viewers do not join rooms. Pending renovation behavior follows
the existing lifecycle implementation; historical migration notes do not override
the current API. Copy and export remain available for readable forms, except
the realization export which has not been implemented (its action stays hidden).

KPPK users share `app_users` identities but edit only their `user_roles` link for
the KPPK project. Their history uses `kppkUsers` and dispatches replay by project,
not just table name: it must never replay a KPPK link through raw-materials user
services. ДЗО grants are not implemented; `orgNames` remains empty/read-only.

## Room

`WS /api/ws/raw-materials?project=raw_materials&section=<section>&reportMonth=<month|empty>&clientId=&sessionId=&ticket=`

- Room: `rm:{project}:{section}:{reportMonth}:{scope}` — «Цены» without a month, «Движение» per month. КППК «Реализация» and «Себестоимость» add the selected ДЗО UUID as `scope`, so edits, leases and presence of different ДЗО don't intersect.
- Auth: signed headers `X-ReportCollector-*`; the browser doesn't send them — instead the query parameter `ticket` (short-lived signed identity). Three rejection reasons: invalid identity (4401), no project access (4403), unexpected Origin (4403) — all logged (`collab.ws_identity_rejected`, `collab.ws_access_rejected`, `collab.ws_origin_rejected`), search the logs by these tags.
- Origin is checked against `BACKEND_CORS_ORIGINS` (see the slash landmine in AGENTS.md).
- Per-connection outbound queue (256) + a dedicated writer task: a slow client doesn't block the room (head-of-line blocking).
- Inbound frames are capped at 64 KiB and 300 messages per ten seconds per
  connection. Oversized frames close with 1009, invalid UTF-8/JSON with 1007,
  and rate abuse with 1008. These are application limits; configure equivalent
  WebSocket/proxy transport caps for frames that the ASGI server must materialize
  before application code sees them.

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

For the renovation plan the published cells include **derived** fields: the formula results (`calculated`). The renovation ID `col_003` is a plain editable cell while the generator is off, so it is published like any typed value (on change, from the normal edit journal). The editor refreshes its window from the PATCH response; every other client only receives `cell:changed`.

## Lease, presence, idle (timings)

| What | TTL | Client |
|---|---|---|
| Cell lease (`lease`) | 20s | renew every 10s |
| Room presence | 25s | heartbeat (`ping`) every 15s |
| Idle detection | 2 min without mouse/keyboard/focus **or** `document.hidden` | leases released immediately (`lease:release`), heartbeat/renew stop; on activity — heartbeat resumes, selection re-sent, server re-joins via `ping` |

Valkey presence stores a server-clock expiration per member, updated atomically by Lua. The room hash TTL is only garbage collection; another user's heartbeat cannot retain an idle member. Each ping broadcasts a current member snapshot, pruning expired members and reconciling missed pub/sub updates. Disconnect removes presence only if the entry still belongs to that `clientId`, so a delayed old connection cannot erase a newer tab's presence. During the upgrade from plain hash payloads, old ephemeral presence entries are discarded and rejoined on the next ping. Restart all application workers together for this format transition (no mixed-version rolling deployment).

Result: walked away (tab open) → after ~2 min leases are released, "online" fades ~25s later. Returned — online again. A "forever" lease is impossible (previously the client renewed endlessly).

Both stores (`ValkeyStore`, `MemoryStore`) implement one interface; the choice depends on `VALKEY_URL` (empty = memory). Lua scripts guarantee owner checks for renew/release.

## What blocks what (lock semantics)

- **`remoteLocks`** — cells **leased** by another user (they're editing): block starting an edit, saving an edit, pasting/cutting a range (such cells are skipped with a «ячейка занята» toast), the paste button.
- **`remoteCellInfo`** — cells from another user's **completed selection** (yellow highlight): block *starting* an edit. Nuance: if I'm already editing a cell (my lease is active) and someone else just selected it on top — my save goes through (lease beats selection).
- **Month fixing** checks `any_active_lease` across all the month's cells → «Фиксация временно недоступна: другой пользователь сейчас редактирует ячейку (имя)».
- **Archive version** — read-only, no presence, the person is "offline".

On connection loss the client drops its whole remote-lease map (`useCollabRoom` clears `lockedCells` whenever the status leaves "online"): `cell:unlocked` events fired during the gap never arrive after a reconnect, and without the reset, released cells would stay blocked in the UI until a reload. Server leases expire by TTL in 20s; live `cell:locked`/`cell:unlocked` events and the fresh `hello` rebuild the map after reconnect.

The client maintains `ownedLeases` (Set) and **must remove keys on release** — otherwise it hits the 1000 limit and leases stop being granted. A delayed presence echo from the same `sessionId` cannot preempt a newer local selection; selection takeover belongs to another browser session.

## Undo/redo (server-side stack)

- Stack is stored in PostgreSQL `collab_edit_stacks`, per user **per browser session and form**: `(user_id, session_id, section/reportMonth)` — «Цены» history doesn't undo «Движение», August doesn't undo September. Survives form switches **within one page load**, but a reload gets a fresh `session_id` and starts with an empty history (stale stacks expire after 24 hours without edits/undo and are periodically deleted; the client re-reads `/undo-state` on form change).
- An action is one PATCH batch — a single request that updates several cells is journaled as one action with a `cells` list (each cell: rowId, field, old/new value, revision; the action carries actionId, targetTable, section, reportMonth) and is undone/redone as a whole. Stack limit 500; lookup covers the full retained stack. Actions in the old single-cell format are still readable (legacy fallback).
- **Record creation is undoable too:** both projects' dictionary create endpoints (raw-materials dictionaries; renovation
  assets and catalogs) journal an «__created» pseudo-cell (new value = the record snapshot; they accept the same `session_id` query parameter as the PATCHes). Undo removes the created record with the same rules as a manual delete — for users, who are never deleted, it deactivates the project link («Активен» = off, assets cleared); redo restores the record from the snapshot with the original id.
  Renovation project users are not journaled yet (their role/block model makes the
  inverse non-trivial).
- **Edit→undo ordering:** data and history now commit atomically. The frontend still waits for its pending PATCH before sending undo: `runUndo`/`runRedo` in `raw-materials-workspace.tsx` first await the in-flight commit promise (`pendingCommitRef`), so undo is only sent after the edit landed on the server.
- **Counter re-sync:** `GET /undo-state` reports only presence (`canUndo`/`canRedo`), so after an applied action — and after `reason=empty` (drift) — the client re-reads it instead of local ±1 arithmetic; a locally decremented counter used to kill the undo/redo buttons after a single press even when the server stack still had actions.
- New edits **clear the redo branch** in the same PostgreSQL transaction.
- **Redo order:** undo removes the freshest action first (4, 3, 2, 1); redo replays undone actions in their **original order** (oldest undone first — 1, 2, 3, 4, the reverse of the undo sequence). `next_action` scans the stack front-first for undo and back-first for redo; the `skipActionId` rule flips accordingly (undo skips `id >= after`, redo skips `id <= after`).
- Applying undo/redo goes through the same `apply_cell_value` + `bump_cell_state` (revisions, `lastActionId`) and publishes `cell:changed`.
- Conflicts: if a cell was changed by someone else after the action (`lastActionId` mismatch **and** `lastUserId` is not the undo author — one's own consecutive edits don't block undoing them) or is leased by another — the action is rejected with `reason=conflict/locked`; a second undo press passes `skipActionId` to skip the blocked one. A batch action is atomic: if any cell conflicts, nothing is applied.
- Final month fixing clears history for the fixed movement scope. Renovation approval clears `renovationPlan/` history. Undo independently rechecks current roles/assets/blocks and version state, so a retained stale action cannot modify an archive.
- **КППК** dictionaries and realization use the same journal. Dictionary mutations are scoped to the tab; realization/cost rooms additionally scope live events and cell authorization to the selected ДЗО. Every mutation carries the page `session_id`. `GET /api/kppk/undo-state?section=&session_id=` (any project member) plus admin-only `POST /api/kppk/undo|/redo` (body `{skipActionId, section}`) walk the stack. The tab component is remounted per section, so switching tabs never shows another tab's history.

## Deployment and WS diagnostics

- The browser connects to `COLLAB_WS_PUBLIC_URL`; empty → same-origin (`wss://<host>/api/ws/...`), nginx proxies to back.
- Symptoms and causes (all are 403 on the handshake, but the logs differ):
  - `collab.ws_origin_rejected` → Origin not in the CORS list. Classic: a slash in `AUTH_APP_ORIGIN` (the browser never sends a slash in Origin). Fix `.env`, recreate containers (`docker compose --profile prod up -d --force-recreate back front`).
  - `collab.ws_identity_rejected` → broken signature/expired ticket (usually after a rebuild with a different `AUTH_SECRET`).
  - `collab.ws_access_rejected` → the user has no access to the project.
  - `ERR_CONNECTION_REFUSED` → nginx doesn't proxy `/api/ws/` or back isn't listening; `ERR_SSL_PROTOCOL_ERROR` → WS goes to the wrong port (not through nginx TLS).
- Diagnostics: `docker logs rc-back --since 30m 2>&1 | grep "collab.ws"`.

`101 Switching Protocols` in the browser Network panel is the successful
WebSocket handshake. The meaningful failure is the later close code. The
backend's normal command Valkey client has a five-second read timeout, while
the collaboration pub/sub listener uses a separate client without an idle read
timeout; otherwise an inactive room would be closed with `1013` every five
seconds. A `1013` close now indicates a real relay/queue or Valkey failure and
is recorded by the collaboration logs/metrics for investigation.

## Tests

`back/tests/test_collab.py` runs against both MemoryStore and real Valkey in CI: protocol, leases/presence, undo scope per session/form and idle-return via ping. `test_store_contract.py` checks concurrent acquisition, atomic numeric revisions, bulk stack order and oldest-first redo on both stores; `test_valkey.py` verifies independent connections and server TTL/pubsub. `front/e2e/collab.spec.ts` runs two-user/multi-tab regressions with local login on the isolated production-image stack. Frontend unit tests cover ticket/session forwarding, lease denial/release, selection preemption and disconnect during ticket fetch (no late socket creation); malformed messages are ignored.

## Transaction and delivery guarantees

- Business writes, undo/redo, cell revisions and outgoing notifications share a PostgreSQL transaction. Services flush, while the function-scoped HTTP dependency commits before sending the response; direct service callers must commit explicitly.
- Valkey remains responsible for expiring leases, presence and pub/sub. Its old journal API is retained only for transport compatibility tests, not used as application history.
- PostgreSQL advisory locks coordinate workers: edits and lease acquisition take a shared project gate and ordered row locks; dictionary mutations and lifecycle transitions take an exclusive project gate. Lease ownership includes browser session. A closed movement period rejects direct PATCH.
- `collab_outbox` delivers committed notifications at least once, in id order among committed records, with retry after Valkey failures. A publish failure does not turn an already saved edit into an HTTP failure. Duplicate notifications are possible. Per-recipient filtering suppresses older cell revisions and rechecks current row access before delivery.
- A room must match the endpoint project and a known section. Dictionary rooms require admin rights; КППК realization/cost rooms require a valid first-day calendar month and a ДЗО UUID scope. An asset worker with zero grants sees zero rows. Central renovation roles see all assets; write permissions still depend on role/block/state.
- Access checks use short-lived SQL sessions, never one connection for the WebSocket lifetime. The subscription is ready before hello. Subscription failure, writer failure or queue overflow closes the socket; reconnect refetches authoritative rows/meta on both forms. No silent dropping of an individual client's queued edits.
- Snapshot reads/exports and renovation archive comparisons apply asset restrictions. Full-plan reads and window counters filter assets in SQL. Workers without grants cannot see unassigned drafts either. Selection and lease payloads are filtered, not only cell-change payloads.
- Raw-material batch edits accept at most 10,000 cells; renovation retains its existing 500-cell limit. Repeated keys within one request form one undo change from the first old value to the last new value.

Regression coverage: `test_backend_guarantees.py` exercises rollback, outbox retry, empty asset grants, central roles, closed periods, same-user/other-session leases, archive undo, permission revocation, duplicate-cell batches, history expiry, readiness and concurrent row recalculation. `test_access_ws.py` covers cross-project/asset rejection, revoked access, relay failure, queue overflow and connection release; it is not a throughput benchmark.
