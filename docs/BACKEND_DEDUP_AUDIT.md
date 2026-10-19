# Backend duplication and quality audit

Reviewed 2026-10-19 against the current `back/src` tree and the complete
backend test suite. This is an audit record, not a claim that every large
domain service should be split immediately.

## Executive result

The backend has a good shared platform boundary for the highest-risk mechanics:

- `src/common/section_policy.py` — capability policy, parity-tested against the
  frontend;
- `src/collab/` — locking, leases/presence, WebSocket protocol, journal and
  undo/redo;
- `src/common/export.py`, `export_jobs.py`, `xlsx_stream.py`, `xlsx_table.py` —
  common validation, streaming jobs and XLSX primitives;
- `src/auth/` — identity, project links and email normalization.

There is still domain-level duplication in routers and services, but most of it
is a legitimate adapter boundary: raw materials, renovation and KPPK have
different tables, permissions, schemas and lifecycle rules. Copying them into a
generic CRUD service would hide business authorization and make undo replay less
safe.

## Findings

### High — no generic project CRUD extraction is safe yet

`raw_materials/router.py`, `renovation/router.py` and `kppk/router.py` repeat
route shapes for list/create/patch/delete, journal, publish and export. The
similarity is structural, but their contracts differ:

- raw materials has movement fixing, asset grants and Keycloak-managed users;
- renovation has typed 216-column plan fields, lifecycle/baseline/asset/block
  permissions and staged plan export;
- KPPK has source-copy uniqueness/restrict rules, realization slices and a
  separate project user-link dictionary.

**Decision:** keep project routers thin but explicit. Only extract helpers that
already have a stable contract (`common export`, `collab journal/events`,
`section policy`). Do not create a universal `ProjectDictionaryRouter`.

### High — largest modules need future seams, not mechanical splitting

Current largest modules include:

- `renovation/service.py` ~3.5k lines;
- `raw_materials/service.py` ~2k;
- `kppk/service.py` ~1.7k;
- renovation/raw/KPPK routers ~0.9–1.5k.

The first safe seams are now implemented: KPPK user administration moved to
`kppk/users.py`, realization reads/edits/replay moved to `kppk/realization.py`,
and `kppk/core.py` owns shared KPPK errors/audit/project-id primitives.
Routers and undo use the domain modules directly; no service-wide generic CRUD
abstraction was added. `kppk/service.py` dropped roughly 600 lines while the
API behavior remains covered by user, realization, WebSocket and undo tests.

The remaining safe seams are domain-specific modules:

1. renovation plan read/edit/lifecycle vs renovation dictionaries;
2. raw movement/prices vs raw dictionaries/users;
3. KPPK source dictionaries vs realization vs project users.

Each seam must preserve transaction decorators, project locks, session ids,
journal target tables and API error mapping. A broad extraction without
contract tests is higher risk than the current duplication.

### Medium — journal/publication boilerplate remains repeated

The routers repeatedly call `record_cell_updates` and `publish_cell_changed`.
KPPK already has `_record_kppk_updates`; raw and renovation have project-specific
variants because their report-month and snapshot semantics differ. A future
`record_and_publish` helper could reduce ceremony if it accepts explicit
project/section/month/user arguments and has tests for empty-applied behavior.
Do not change this during the KPPK users review because it touches every write
endpoint.

### Medium — user administration has a shared identity but project-local links

The same `app_users` table is used by all projects. This is correct, but the
mutations must stay project-scoped:

- raw users edit raw `UserRole`/asset grants;
- renovation users edit renovation role/block/grants;
- KPPK users edit only the KPPK `UserRole` link and never org grants.

The new `kppkUsers` replay branch explicitly dispatches by project before
touching `app_users`. Self-edit protection is enforced in the UI, HTTP routes,
WebSocket cell authorization and undo authorization.

### Medium — policy is now a real parity contract

`test_frontend_policy_matches_every_backend_mode` compares the frontend policy
source to backend `SECTION_POLICIES`. Workspace gates use the policy for room,
undo/export and row-operation visibility while lifecycle/asset/field checks
remain domain-local. This is the correct balance: policy controls capability,
server services control authorization.

### Low — generated quality scores overstate smell count

The generic Python checker reports many long functions and gives the largest
services poor scores. That is useful as a seam map, not an automatic refactor
list. It does not understand transaction boundaries, domain invariants or the
intentional project adapters. Ruff, ty, tests and explicit endpoint contracts
are the current acceptance gates.

## Query/transaction review

- The user/member list queries use project-link joins and the project index.
- KPPK realization reads use source-copy filters and deterministic product/market
  ordering.
- Shared exports validate client columns server-side and use the common job
  mechanism.
- Journal replay locks logical rows in stable order and checks leases/current
  permissions before applying values.
- No new unbounded per-row network loop was introduced by KPPK users; seeding
  administrators is two deterministic link checks.

The next performance investigation should use production-like query plans and
latency targets rather than further static deduplication.

## Verification

- Ruff check/format and ty pass.
- Full backend suite: 403 passed, 4 skipped in the current review state.
- Targeted KPPK users/policy/self-edit tests pass.

## Recommended next work

1. Keep this audit and split only after adding contract tests per proposed seam.
2. Add API contract tests for KPPK user update/export/undo as the UI is reviewed.
3. Measure slow plan/realization queries with EXPLAIN and production-like data.
4. Revisit router boilerplate only after the project API contracts stabilize.
