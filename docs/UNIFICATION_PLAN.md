# Unifying the workspace mechanics across projects

Status: **in progress**. Started 2026-10-17. This file is the working log — it records
the agreed rules, the stage plan, progress and open questions. Update it as stages land;
delete it (or fold it into `docs/GRID.md` / `docs/COLLAB.md`) once the work is done.

## Agreed rules (user decisions, 2026-10-17)

1. **Collaboration everywhere except non-editable tables.** Every project and every form
   has the same real-time collaboration (leases, presence, live `cell:changed`, selection
   sharing). Non-editable tables — the fixed movement month, the movement version archive,
   the submitted/approved renovation plan, archived plan versions — show **no** collaboration
   at all (no room, no presence, no leases).
2. **Editing works the same everywhere**, with the same shortcuts and the same cell-selection
   rules. Role restrictions on *which columns* a limited user may edit stay a role-model
   concern; an **admin edits everything except what is fixed** (fixed month, archive,
   submitted/approved plan are a hard boundary for every role).
3. **Ctrl+A is disabled in "large" tables — the tables loaded in chunks.** Expressed as the
   grid's loading model (`chunked`), not as a guessed row count: today only the renovation
   plan is chunked; a dictionary that later becomes chunked falls under the rule
   automatically.
4. **Undo/redo is available to every user who can act on the given form right now**
   (current write permission to the form; the row/column checks stay as they are). Users
   without write access see no undo/redo — there is nothing for them to undo. Undo of a
   record must respect the same state checks as a live edit (no undo into an archive or a
   submitted/approved plan).
5. **Copy and export are available everywhere, for every role**: every role exports what it
   can see (server-side asset/visibility filtering applies — export is not a leak channel).
   Dictionaries are exportable by every member.
6. **Row delete and row add are undoable.**
7. **КППК gets real-time collaboration** like the other projects.
8. **Header pinning and service columns are common to every project**, modelled on
   «Ремонт» — including the reset that also drops the **modified header height**. The pin
   state is remembered **between sessions** (`localStorage`).
9. **The form status (`FormStatus`) is common to every project** and looks like «Ремонт»,
   including its selected-cells rendering (column numbers / header chain, detail on hover).
   It also shows no presence on non-editable tables.

## Current state (audit result, 2026-10-17)

See the matrix in the audit report; the short version:

| Form | Collab | Clipboard | Undo | Export | Header |
|---|---|---|---|---|---|
| Движение / Цены | full | full | admin-only | all | one pair, no height reset |
| Сырьё dictionaries | admin room | full | admin-only | admin-only | same pair |
| Users (Сырьё) | admin room | full | admin-only | admin-only | same pair |
| Movement archive | off (correct) | copy only | hidden | all | same pair |
| Ремонт plan | full | no Ctrl+A | admin-or-not, approver hidden | job | own pair + height reset |
| Ремонт dictionaries | admin room | full | admin-only | admin-only | own pair |
| КППК (3 tabs) | **none** | full | admin-only | all | own pair |

Known gaps found while auditing: raw dictionary DELETEs are not journaled; renovation plan
row add/assign/delete and renovation user create are not journaled; the "dictionary = admin
room" rule lives in `collab/access.py`; the undo admin gate is in `collab/undo.py`
(`_authorize_action`); `parsePastedText` is dead code with a duplicated inline trim;
three copies of the undo-race guard; three places holding the pin state.

## Stages

| # | Stage | Content | Status |
|---|---|---|---|
| 0 | Section policy | one declarative source of truth (`collab` / `undo` / `export` / `chunked` / `paste` / `rowOps`) read by front and back | **done** |
| 1 | Backend journaling | journal raw dictionary deletes, plan row add/assign/delete, renovation user create (`__created`/`__deleted`); member undo with the current-write check | **done** |
| 2 | Collaboration everywhere | КППК WS room (`canonicalCells` first), renovation dictionary presence, dictionary-admin rule moved into the policy | **done** |
| 3 | Front hooks | `useUndoRedo(scope)`, `useExport(section)`, single paste parser, Ctrl+A by policy | pending |
| 4 | Header & status | one pin/service-columns mechanism per project with height reset + persistence; `FormStatus` common, renovation look | **done** |
| 5 | Verification | unit + backend tests, per-project e2e, docs | pending |

- 2026-10-18 — **stage 1, part 1: deletion and row lifecycle are undoable.**
  - **«Сырьё» dictionary deletes** (`DELETE /raw-materials|assets|delivery-bases/{id}`) now take
    `session_id`, return an «__deleted» snapshot from the service (name, asset links / user
    grants, audit stamps) and journal it through `_record_dictionary_delete`, which also
    publishes `rows:changed`. `apply_cell_value` learned `__deleted`: undo restores the row
    with its original id and links, redo deletes it again through the same service (business
    rules still apply).
  - **Renovation plan rows**: `POST /plan/rows` journals «__created», `DELETE /plan/rows/{id}`
    journals «__deleted» (position, plan, asset, baseline generation, audit), and
    `PATCH /plan/rows/{id}` (asset assignment of a draft) journals the `asset_id` cell.
    `apply_renovation_cell_value` dispatches those pseudo-fields to `_apply_plan_row_change`;
    ordinary plan cells keep their old path. `_authorize_action` now resolves the plan from
    the snapshot when the row itself is gone, and treats row-lifecycle fields as "any current
    plan editor" (the approver is still refused; archived/pending plans are guarded as before).
  - Tests: `test_dictionary_delete_undo.py` (4), `test_plan_row_undo.py` (3),
    `test_undo_rights.py` (1). Backend: **382 passed, 4 skipped**.
  - Still open in stage 1: renovation `POST /dictionary/users` (user create) is not journaled.
- 2026-10-18 — **stage 1 closed + real user deletion in every project.**
  - **Creating a user in «Ремонт»** now journals a `__created` snapshot with role, block,
    assets and the audit name, so its undo removes the person and redo restores them.
  - **A user can be deleted, not only deactivated** — in all three projects:
    - `DELETE /raw-materials/users/{id}` — the «Сырьё» link and the per-asset grants go,
      journaled as an undoable «__deleted» (`_apply_deleted_user` restores link + grants);
    - `DELETE /renovation/dictionary/users/{id}` — the renovation link and its asset grants
      go, journaled the same way (`restore_renovation_user`);
    - `DELETE /kppk/users/{id}` + `GET /kppk/users` (admin-only member list) — the КППК
      subscription (`UserRole` link) goes; no undo journal for user links yet.
    - **The account is soft-deleted when no project link is left**
      (`_soft_delete_if_orphaned`), so the person really disappears from every
      «Пользователи» list instead of lingering as an empty row — while the seed
      self-provisioning still recreates a *link* on the next project URL visit, so access
      has to be re-enabled by an admin.
  - Tests: `test_user_delete.py` (5, all three projects + the "keeps the account while
    another project needs it" rule). Two old tests that asserted "user deletion is not
    supported" were updated to the new behaviour. Backend: **387 passed, 4 skipped**.
  - Remaining follow-up: the front end has no «Удалить пользователя» button in the КППК and
    «Ремонт» user tabs yet (the backend endpoints and the «Сырьё» one, which already had the
    button wired to the same DELETE, are ready).
- 2026-10-18 — **protective delete rules for users.**
  - **A user cannot remove themselves** from a project — refused with `409` in all three
    projects (`GET /api/me` carries no id, so the front matches the grid row by email).
  - **A user whose access to the project is still enabled cannot be deleted either**: the
    admin revokes access first (the «Доступ»/«Активен» flag), then deletes. Also `409`, with
    a message telling exactly that.
  - The action-bar delete button **stays visible but disabled** in both cases (never hidden),
    and `ActionItem` gained `disabledReason`, rendered as the button's native tooltip — so the
    user sees *why* it is blocked. The backend enforces the same two rules.
  - Tests: `test_user_delete.py` grew to 7 (self-delete refused in all three projects; a
    user with access refused; the old tests now revoke access before deleting). Backend:
    **389 passed, 4 skipped**; front unit 89 pass.
- **Landmine (cost a debugging detour):** bulk `-replace` on a source file with PowerShell
  rewrote `back/src/collab/undo.py` as UTF-8 **with BOM and mojibake** for every Cyrillic
  string. `ruff` did not catch it — only the broken Russian error text did. Any scripted edit
  of sources must keep UTF-8 without BOM and be verified (`*_check_encoding.ps1` pattern)
  before running tests. Prefer the `edit` tool for anything containing Cyrillic.
- **Landmine (regression found and fixed):** the new `apply_renovation_cell_value` branch
  matched **every** `renovation_plan_rows` cell before checking the field, so ordinary plan
  cell undo started failing. The branch must be guarded by
  `field in ("__created", "__deleted", "asset_id")`.

## Progress log
- 2026-10-17 — audit of the three workspaces and the backend finished; the rules above were
  confirmed by the user (points 1–9). Stage order fixed: policy first, then backend, then
  hooks, then header/status.
- 2026-10-17 — **stage 0 (policy) done** on both sides:
  - `front/src/shared/grid/section-policy.ts`: `SectionPolicy` (collab / undo / export /
    rowOps / chunked) for every section, including `renovationPlan` and the dynamic
    `renovationCatalog:*`; helpers `sectionPolicy`, `isChunkedSection`, `isCollabSection`,
    `undoAvailableFor`, `exportAvailableFor`, `readOnlyPolicy`.
  - `back/src/common/section_policy.py`: the backend twin, same sections and modes.
  - `back/src/collab/access.py`: the "dictionary room is admin-only" rule now reads
    `admin_room_section(section)` instead of a hardcoded set.
  - `back/src/collab/undo.py`: `_authorize_action` consults `section_policy` for dictionary
    cells (admin-only dictionaries stay admin-only; the member case is ready for a
    dictionary that becomes member-editable).
  - Tests: `back/tests/test_section_policy.py` (6) and
    `front/src/shared/grid/__tests__/section-policy.test.ts` (6) — both green.
  - Not yet wired: the workspaces still use `MAIN_SECTIONS` / `isAdmin &&` directly.
    That happens in stage 3 together with the hooks.
- 2026-10-17 — **stage 4 (header, service columns, form status) done**:
  - `front/src/shared/ui/use-view-state.ts`: one `useViewState(projectKey)` hook for
    `headerPinned` + `showServiceColumns` with `localStorage` persistence per project
    (`reportcollector:view:<key>`, field-by-field fallback, corrupted JSON tolerated).
    Wired in «Сырьё», «Ремонт» (one pair for plan + dictionaries instead of two) and КППК.
  - Reset label is «Сброс ширины столбцов и высоты заголовка» in every project (the plan
    already reset its header level heights; the label is now uniform).
  - `front/src/shared/grid/selection-label.ts`: `describeSelection` — the footer names the
    selection in the renovation style («столбец 2, строка 3», «столбцы 2–3, строки 1–5»)
    with the header chain as `detail`. The shared grid reports `detail`; «Сырьё»/КППК pass
    it to `FormStatus` (`selectionDetail`), so the status looks the same everywhere.
  - `FormStatus` itself needed no markup change — it was already common; the difference was
    only in what each workspace fed it.
  - Tests: `front/src/shared/ui/__tests__/use-view-state.test.ts` (5),
    `front/src/shared/grid/__tests__/selection-label.test.ts` (4). Front: 82 tests, typecheck
    and lint clean.
  - Not done yet: the pin state is not applied to the *grid's* stored column widths reset
    beyond the existing `resetColumnWidths` (the raw/КППК grids have no header-level heights
    to drop — only the plan has levels); and the КППК dictionaries still render no presence
    (stage 2).
- Landmine: importing `raw-materials-grid.tsx` in a happy-dom unit test crashes on the
  revogrid loader (`Attempted to assign to readonly property` at import). Pure helpers used
  by tests live in separate modules (`selection-label.ts`), not in the grid file.
- 2026-10-17 — **stage 2 (collaboration everywhere) — backend and КППК front done**:
  - `back/src/collab/access.py`: `KPPK_SECTIONS` (three dictionaries, with the editable
    field sets) and `_sections_for_project()`; the room resolver no longer hardcodes two
    projects and picks the table map by project key.
  - `back/src/collab/ws.py`: `WS /api/ws/kppk` (default section `kppkPositions`,
    `_resolve_kppk_access`), `NoKppkAccessError` handled in the access rejection paths.
  - `back/src/kppk/router.py`: `_record_kppk_updates` now also calls `publish_cell_changed`
    (journaling stays conditional on `session_id`, publication does not); undo/redo
    publishes its applied cells through `_undo_response` (now async).
  - `front/src/shared/collab/ws-client.ts`: `kppk → /api/ws/kppk` (the old raw-materials
    mapping was dead code).
  - `front/src/features/kppk/kppk-workspace.tsx`: the tab joins its room when the user is an
    admin (`KPPK_EDITABLE_FIELDS` → `canonicalCells`), merges remote `cell:changed` values
    into the rows, shows leases/presence, sends the selection, and publishes its room to the
    footer `FormStatus`.
  - Tests: `back/tests/test_kppk_ws.py` (5) — sections, admin joins every room, a member is
    denied an admin-only room, unknown section rejected, another project's socket rejected.
    Backend: **374 passed, 4 skipped**.
  - Remaining in stage 2: the renovation **dictionaries** still don't render presence and
    don't distinguish the user/asset in presence (the plan does). Not started.
- 2026-10-17 — **stage 2 closed**: the renovation dictionaries now behave exactly like every
  other grid. `visibleAssets` (the plan's asset-scope filter for *who is shown*) is passed
  only for the plan — the dictionaries have no asset dimension, so they show everyone, like
  «Сырьё» and «КППК». The dictionaries also report their selection label to the footer
  `FormStatus` (`onSelectionLabelChange` → `RenovationDictionarySection` → `RawMaterialsGrid`
  `onSelectionChange`), which they previously never did. The role caption («админ» /
  «Утверждающий» / «Ответственный за блок» / assets of an asset worker) stays in the shared
  `memberDetail` — it is not a renovation-specific split. Front: 83 tests, typecheck and
  format clean.
- 2026-10-17 — **footer statistics made common** (follow-up of stage 4): the renovation
  dictionaries never passed `onViewStatsChange` to the grid, so the footer got no
  «Строки: найдено / всего» and no «Столбцы» breakdown there; the footer also dropped the
  stats outright when leaving the plan (`gridStats={isPlanSection ? gridStats : null}`).
  Now `RenovationDictionarySection` takes a required `onGridStatsChange`, the workspace keeps
  a separate `dictGridStats`, and `FormStatus` renders both tabs' numbers. «Сырьё» and КППК
  already wired the same callback — the shared grid computes the numbers in one place
  (`measureViewport`: `filteredRows` from `getVisibleSource("rgRow")`, `totalRows` without
  the placeholder and deleted rows). Inline «Строки» still appears only with active filters;
  the hover breakdown always shows both — the renovation plan's rule, now everywhere.
- Landmine found while testing: two backend pytest runs in parallel hit the same PostgreSQL
  test schema and fail with `duplicate key value violates unique constraint
  "ix_projects_key"`. Run backend tests one at a time.
- 2026-10-19 — **shared loading skeleton + КППК filter row**. Every grid's first-load state is
  now one component, `front/src/shared/ui/grid-loading-skeleton.tsx` (`GridLoadingSkeleton`):
  a miniature of the table — a «№» column and a wide data column, a 1px rule between every
  block (`gap: 1px` on the cell-border colour), a taller header row and four data rows — instead
  of the old spinner over a grey `Skeleton`. It is wired into «Сырьё» (meta + rows),
  «Ремонт» (meta + plan window), «КППК» (rows) and «Реализация» (no ДЗО yet + pending).
  Background refreshes keep the grid mounted and never show it. Landmine: the per-row
  markup must not be a shared JSX node reused across rows — React keys then collide.
  **КППК «Реализация» toolbar**: the period filters moved to a second row
  (`.table-toolbar--stacked` + `.table-toolbar__row`) BELOW the tabs, so they open by growing
  height (`.kppk-realization-filters-row`, `max-height` + fade) rather than sliding in from
  the left; the gap to the tabs is half the usual rhythm and the row keeps the ordinary
  toolbar-to-grid padding below it. The month options are lowercase («за январь 2026»), and the
  three filters take `compactBelow="sm"` — short labels only below 40rem, full values above.
  `SmartSelect` grew the `compactBelow` and `maxPopoverWidth` props; its open list is now
  **always** as wide as its longest option (measured on a canvas, `--smart-select-width`,
  floor 14rem, nowrap), never the trigger width — the «Бизнес-план» two-line bug came from
  HeroUI sizing the panel to `--trigger-width`. A `maxPopoverWidth` cap exists for lists of
  user data (the ДЗО filter, 30rem); curated lists get none. The КППК
  `GET /api/kppk/realization` now maps `KppkValidationError` to `400` — picking a year absent
  from «Периоды» (2027) used to be a `500` (`test_kppk_api.py::test_realization_missing_period_is_a_bad_request`).
  Merged `CellMergePlugin` cells are vertically centered
  (`.raw-materials-grid revogr-data .rgCell[merged]`), so the product label sits mid-run.

## Notes / risks

- **Pre-existing failure (not ours):** `tests/test_renovation_api.py::test_export_working_plan_renders_merged_header`
  fails with `KeyError: 217` in `renovation/export.py::_number_row_xml` even on the untouched tree
  (verified with the stage-0 changes stashed). It looks like a header-layout/column-number mismatch
  in the plan writer; needs its own investigation and is unrelated to the unification.
- Stage 1 is the riskiest: undoing a row add/delete must go through the same plan-state
  checks as live editing. Undoing into an archive/submitted plan must be impossible and is
  covered by tests.
- Stage 2 for КППК is not just "open a socket": the KPPK grid does not build
  `canonicalCells`, so leases have no keys until that is added.
- Stage 4 touches layout and `globals.css`. The renovation plan has its own 11-level overlay
  header — the *behaviour and state* become common, the markup does not.
