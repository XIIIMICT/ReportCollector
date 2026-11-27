# Batched lease round trips (P1-2)

Date: 2026-11-27. Branch `perf/batched-lease-lua`, base `8ab3ebe` (dev head).
Every quantity below is tagged **MEASURED** (read from a command output or a
cited source line), **DERIVED** (arithmetic on measured numbers) or
**UNMEASURED** / **DOCUMENTED**. No production data or dev stand was changed.

## Verified lease and token model (MEASURED)

Research was done before any Lua was written; the "fencing token = Valkey lease
value" hypothesis in the task was **disproved**.

| Fact | Verified source |
|---|---|
| Lease key `rc:lease:{project}:{table}/{row}/{field}`, TTL **20 s** | `back/src/collab/store.py:29,36-38` |
| Owner value is a JSON **dict** (`userId`, `name`, `email`, `sessionId`, `clientId`, `assets`, `role`, optional `block`), serialized with `json.dumps(..., sort_keys=True)` | `back/src/collab/ws.py:452-464`, `store.py:68-69` |
| Acquire = `SET NX EX`; a denial returns the **current owner dict** (second `GET`) | `store.py:247-253` |
| Renew/release = Lua `GET == owner` then `EXPIRE` / `DEL`; no numeric token | `store.py:80-93,264-272` |
| The numeric fencing token is the **PostgreSQL sequence** `collab_action_id_seq` → `lastActionId` in `collab_cell_states`, together with the monotonic per-cell `revision` | `collab/models.py:12`, `collab/persistence.py:53-54,118-164` |
| PG write path rechecks leases with one `MGET` and rejects a different `userId` **or** `sessionId` (`CellValidationError` → HTTP 400); journaling repeats the check as HTTP 409 | `raw_materials/service.py:581-591`, `collab/journal.py:45-65` |
| Undo conflict uses `lastActionId` **and** `lastUserId`, then rechecks the lease | `collab/undo.py:339-371` |
| `ValkeyStore.next_action_id()` (`rc:actionid` INCR) and `bump_cell_state()` are the non-transactional interface fallbacks; the application write path uses `JournalStore`, which overrides both with PostgreSQL | `store.py:311,407-420`, `persistence.py:53-54,118-164` |

Consequence: batching Valkey lease calls **must not invent or move a token**.
The end-to-end fence remains PostgreSQL sequence → `lastActionId` → undo
conflict check, plus byte-identical owner-dict equality in Valkey. The tests in
`back/tests/test_lease_batching.py` assert action ids and per-cell revisions are
strictly monotonic across repeated batch calls.

### Valkey capabilities (MEASURED on the disposable CI stand)

- `valkey-cli info server`: `valkey_version:8.1.11`, `redis_version:7.2.4`
  (Redis compatibility field), `valkey/valkey:8-alpine` image.
- `valkey-cli command info eval evalsha script` reports all three commands with
  the `@scripting` flag and `noscript` behaviour; `SCRIPT LOAD`/`FLUSH`/`EXISTS`
  are available.
- `valkey-py 6.2.0` `AsyncScript.__call__` sends `EVALSHA` first and on
  `NoScriptError` calls `SCRIPT LOAD` and retries once with the returned SHA.
  This is the deterministic fallback used by both batch scripts
  (`store.py:235-238`). `back/tests/test_valkey.py` forces it with `SCRIPT FLUSH`
  and asserts exactly one load per script.
- Script replication (**DOCUMENTED**, not measured — the stand has
  `connected_slaves:0`): Valkey 8 replicates the *effects* of a script (the
  `SET`/`EXPIRE`/`DEL` commands it issues), not the script text, so scripts must
  be deterministic. Both batch scripts derive every write from `KEYS`/`ARGV`
  only; no `TIME`, randomness, or external state. There is no
  `lua-replicate-commands` setting in 8.1.11 to change that behaviour.

## What changed

- `back/src/collab/store.py`: two Lua scripts, `_BATCH_ACQUIRE_LEASE_SCRIPT`
  (one `SET NX EX` verdict per key) and `_BATCH_RELEASE_LEASE_SCRIPT` (one
  owner-guarded `DEL` verdict per key), plus `acquire_leases()` /
  `release_leases()` on both stores. An empty key list short-circuits before any
  script call. The single-cell methods remain available and unchanged.
- `back/src/collab/ws.py:708-736`: `lease:acquire` and `lease:release` issue one
  batched call for the whole message's cell set; `lease:renew`, disconnect
  cleanup and all per-cell publications are unchanged.
- No dependency, protocol, schema, migration or workflow changes.

## Semantics preserved (verified by tests)

| Contract | Preserved how |
|---|---|
| Per-cell TTL 20 s | One `EX 20` per key inside the acquire script (`ttl` is an ARGV); the Valkey test asserts each key's `PTTL` after a batch and that a denied acquire does **not** renew a lease |
| Partial grants / denials | Script returns one verdict per cell; `lease:granted`, `lease:denied` and one `cell:locked` per granted cell are unchanged |
| Owner equality | The owner JSON is the exact same `ARGV[1]` bytes used by the old Lua equality checks; wrong owners get `False` on release |
| Monotonic fencing tokens | No PG sequence or `revision` write moved; `test_lease_batching.py` asserts increasing ids/revisions across repeated batch calls |
| Error types | Oversized (>500) and >1000-owned selections keep the same Russian `error` frames; transport failures still surface as `Хранилище совместного редактирования временно недоступно.` |
| Both backends | `MemoryStore` has the same batch API under one asyncio lock; the full pytest suite ran in both memory and valkey modes |

## k6 before/after (MEASURED)

Stand: disposable CI (`back` `127.0.0.1:19000`, front `16000`, Valkey
`17379`, PG `16432`), two Granian workers, `KEYCLOAK_ENABLED=false`, reserved
`user1@test.local`, 67 movement rows, `E2E_TEST_PASSWORD` set on that stand
only. Before = the pre-existing CI image with unmodified lease code (its exact
source commit was not recorded when that image was built, so this is **NOT** a
commit-pinned comparison); after = the same stand after rebuilding `rc-back:ci`
from this branch and recreating only the `back` container. Both runs used the
same commands.

Common environment (PowerShell):

```powershell
$env:E2E_TEST_PASSWORD = 'LoadBaseline2026'
$env:RC_FRONT_URL = 'http://127.0.0.1:16000'
$env:RC_WS_URL = 'ws://127.0.0.1:19000/api/ws/raw-materials'
$env:RC_REPORT_MONTH = '2026-08-01'
$env:RC_VUS = '1'; $env:RC_DURATION = '12s'; $env:RC_ROWS = '67'
foreach ($k in @(1,5,20)) {
  $env:RC_CELLS = "$k"
  $b=[int]((docker exec reportcollector-ci-valkey-1 valkey-cli info stats |
    Select-String 'total_commands_processed:(\d+)').Matches[0].Groups[1].Value)
  $sw=[System.Diagnostics.Stopwatch]::StartNew()
  k6 run load/steady-edit.js
  $sw.Stop()
  $a=[int]((docker exec reportcollector-ci-valkey-1 valkey-cli info stats |
    Select-String 'total_commands_processed:(\d+)').Matches[0].Groups[1].Value)
  "k=$k delta=$($a-$b) window=$([math]::Round($sw.Elapsed.TotalSeconds,2))s"
}
```

`ws_lease_granted_rtt_ms` (k6 trend, milliseconds; p50 = `med`). All runs had
100% checks, 0 failed PATCHes and no denied cycles:

| k | Before p50 / p95 / p99 | After p50 / p95 / p99 | Cycles before → after |
|---:|---|---|---|
| 1 | 9 / 37.29 / 43.60 | 9 / 19.59 / 44.03 | 95 → 99 |
| 5 | 12 / 21.04 / 41.25 | 9 / 19.29 / 46.64 | 80 → 88 |
| 20 | 15 / 50.00 / 54.00 | 10 / 22.84 / 61.98 (max 140) | 81 → 84 |

Valkey `INFO stats` `total_commands_processed` delta (includes the sampler's
own two `INFO` calls and commands executed **inside** Lua):

| k | Before Δ / window / OPS | After Δ / window / OPS | Commands per cycle before → after (DERIVED) |
|---:|---|---|---|
| 1 | 2686 / 12.66 s / 212.2 | 2908 / 12.79 s / 227.4 | 28.3 → 29.4 |
| 5 | 4506 / 12.66 s / 356.0 | 4689 / 12.75 s / 367.8 | 56.3 → 53.3 |
| 20 | 13069 / 12.66 s / 1032.3 | 12041 / 12.68 s / 949.6 | 161.3 → 143.3 |

Interpretation (DERIVED): the code path went from `k` sequential `SET NX` +
one `GET` per denied cell (acquire) and `k` sequential `EVAL` calls (release)
to one `EVALSHA` each. The gained time is the removed network round trips; the
command counter barely moves because Valkey counts the `SET`/`EXPIRE`/`DEL`
issued inside the script too. Marginal cost drops from ~7.0 to ~6.0 commands
per extra cell per cycle. p99 at k=5/k=20 is noisier than p95 at 1 VU — those
exact values are reported, not smoothed; the k=20 after run had one 140 ms
outlier. No SLO claim is made. The N=20 / 30 s baseline in
`docs/LOAD-BASELINE.md` (lease p95 346.64 ms) was **not** re-run here, so a
before/after comparison at N=20 is **UNMEASURED**.

## Validation summary

- `ruff check`, `ruff format --check`, `ty check`, `prek run --all-files`: green.
- Full pytest: 817 passed / 17 skipped (memory, 81% coverage), 826 passed /
  8 skipped (valkey, 80% coverage); combined coverage **81%** with `--fail-under=80`.
- `bun run --cwd front typecheck` (290 files) and `bun run --cwd front test`
  (732 tests): green. `docker compose -f infra/compose.ci.yaml config --quiet`:
  green. No workflow was touched, so actionlint was not applicable.
