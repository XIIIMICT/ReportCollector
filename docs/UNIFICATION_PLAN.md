# Workspace unification: implementation status

Reviewed 2026-10-19. This replaces the historical progress log, whose interim
statuses and requirements no longer described the current implementation.

## Implemented for exposed features

| Area | Implementation / verification |
|---|---|
| Static section capabilities | `back/src/common/section_policy.py` and `front/src/shared/grid/section-policy.ts` declare room/history/export/row-authoring/loading modes. `test_frontend_policy_matches_every_backend_mode` compares every id and mode across the actual files. |
| Workspace policy consumers | Raw-materials, renovation and KPPK use collaboration/history capability helpers; row-authoring and delete visibility use policy where applicable. KPPK checks export capability, including admin-only users. Static helpers supplement, never replace, lifecycle/role/asset/column checks. |
| Large table select-all | Both grids consult `chunked`; renovation plan Ctrl+A remains swallowed, including revo-grid's built-in path. |
| Journaled row lifecycle | Raw-material dictionary deletion, renovation row add/assign/delete and renovation user creation use the shared journal. KPPK users creation/edit/delete also replay through a project-specific dispatcher despite sharing `app_users`. |
| Collaboration | KPPK dictionaries/realization and renovation dictionaries use canonical keys, shared rooms, leases, presence and updates. Dictionary rooms remain admin-only, matching write access. Immutable archive viewers do not join rooms. |
| Shared history/export execution | `history-action.ts` and `table-export-action.ts` centralize event processing without effects that publish fresh objects into parent state. API scopes/session ids and pending-write refs remain local. |
| Clipboard | Both grids reuse `parsePastedText`. `use-grid-clipboard-actions.ts` preserves callback identity and resolves the current grid handle at click time. Parent publishers compare callbacks as well as availability so tab switches do not retain stale actions. |
| Header/status | `useViewState` persists pin/service-column state per project; resets retain project-specific header-height handling. `FormStatus`, selection labels, skeletons, feedback and workspace/fullscreen layout are shared. |

## Current product boundaries (not unfinished refactors)

- Permission to read is not permission to edit. KPPK and renovation reference
  dictionaries are admin-edited; raw forms retain asset checks; renovation
  retains block/asset/lifecycle checks. Server authorization is authoritative.
- Pending renovation behavior follows the current API, not the legacy migration
  handoff: pending-state and immutable archive checks are distinct. This pass
  does not silently redefine the approval state machine.
- Copy/export remain available for readable implemented forms. Realization
  export has not been implemented, so its export action stays hidden; a static
  policy entry alone cannot create an API. Realization row operations are absent
  because rows are derived from dictionaries/source data.
- KPPK users: admin-only tab and API, administrator assignment, empty read-only
  ДЗО association. Identity/password fields are managed by Keycloak in SSO mode.
  Editing KPPK membership must not alter another project's roles or asset grants.
  This feature is **uncommitted pending user review**.
- Production-scale Excel/real-Valkey/load exercises are explicit release checks,
  not prerequisites for extracting a hook and not claimed by local unit tests.

## Verification

- Frontend: typecheck, oxlint/stylelint, formatting; 117 unit tests passed.
- Backend: Ruff/format/ty; full local suite 396 passed, 4 skipped (memory store).
  Additional users/policy tests cover replay direction/scope, shared-user project
  isolation, empty ДЗО export and rejection of unsupported organization grants.
- Authenticated browser: KPPK users list, add modal, tab roundtrip and absence of
  realization deletion; no console errors. No real account was created by this
  UI inspection. Prior smoke checks also exercised movement/prices, renovation
  plan/enterprises, all KPPK tabs, slice change, fullscreen and Excel download.
- Not rerun here: the complete multi-user E2E/real-Valkey matrix and a production-
  size workbook export. These must not be recorded as completed from unit tests.

Durable operational guidance is in `GRID.md`, `COLLAB.md` and `WORKFLOW.md`.
The completed frontend extraction audit has been removed; the renovation legacy
handoff remains in `RENOVATION_MIGRATION_CONTEXT.md` with a historical warning.
