# Frontend deduplication and render stability

Completed extraction plan for the large raw-materials, renovation and KPPK
workspaces. This document describes the current implementation, not a promise
to generalize project-specific business logic.

## Completed

| Area | Shared implementation | Consumers / boundaries |
|---|---|---|
| Remote cell maps | `shared/collab/use-remote-cell-maps.ts` | Renovation dictionaries reuse the existing identity-stabilized maps instead of rebuilding them on each presence heartbeat. |
| Text/row helpers | `shared/ui/text-helpers.ts` | Error messages, placeholder text, record labels and grid-row envelopes; KPPK retains local text helpers where independently developed. |
| Shortcuts | `shared/ui/workspace-shortcuts.ts` | Delete and undo/redo use physical key codes and `isFromTextEditor`, including shadow-DOM editors. Permission gates remain local. |
| History availability | `shared/ui/undo-counters.ts` | Raw-materials and renovation re-read server availability instead of incrementing/decrementing local stack lengths. |
| Undo/redo execution | `shared/ui/history-action.ts` | Four call sites share pending-write waiting, direction-specific skip ids, result interpretation and toasts. API scope, session-bearing API wrappers and write refs stay local. Business-rule conflict fields are explicit parameters. |
| Export execution | `shared/ui/table-export-action.ts` | Raw-materials, KPPK dictionaries and renovation dictionaries share the grid read, pending lifecycle, Blob/job download and error reporting. Existing callback dependencies and parent-publication effects remain local. Renovation plan export stays separate: its filters are server-side and it is job-only. |
| Action groups/dialog | `shared/ui/action-items.tsx` | Clipboard/history/export/add/delete/lifecycle action factories and the shared delete confirmation. No rendered component is imported from a different project's workspace. |
| Excel paste parsing | `shared/grid/grid-clipboard.ts` | Both grids use the existing `parsePastedText`: trim trailing blank rows/columns without removing interior blanks. |
| Grid warnings | `shared/ui/grid-warnings.ts` | Module-level handlers preserve identity; the existing lock-message wording is an explicit parameter. |
| Clipboard publication | `shared/ui/use-grid-clipboard-actions.ts` | All five grid consumers use stable callbacks that resolve `gridRef.current` at click time. `updateClipboardActions` ignores equal publications but accepts changed handlers when switching grids. |
| Selection publication | `shared/ui/text-helpers.ts` | `publishSelection` centralizes footer label/detail and room updates for raw-materials/KPPK. Raw-materials preemption suppression stays explicit. Renovation's content-deduplicated selection publisher remains local. |
| Loading/errors | `shared/ui/grid-loading-skeleton.tsx`, `workspace-feedback.tsx` | Shared initial-load skeleton and common error cards. Background refreshes keep grids mounted; feature-specific messages remain local. |
| Workspace layout | `shared/ui/workspace-layout.tsx` | Shared body/card/content and fullscreen reveal panels. Project workspaces still own toolbar, grid, dialogs and profile state. `ProfileModal` itself was already shared; its few prop bindings do not need another abstraction. |
| Select collection | `shared/ui/smart-select.tsx` | Memoized item elements and open-state notifications independent of callback identity. Custom listboxes and special options are retained. |

All actionable clusters from this audit are addressed. The retained differences
below are intentional, not unfinished items.

## Render-loop incident and regression prevention

The first clipboard extraction returned a new handler on every render. The
grid's effect depends on `onClipboardStateChange` and publishes `{ canPaste }`;
the handler published new action closures into workspace state. The resulting
workspace render created another handler, restarting the effect indefinitely.
Stacks surfaced at both the grid's paste-availability calculation and HeroUI
`ListBox.Item` collection updates. A stack location alone did not identify the
source of the loop.

Comparing only `canPaste` stopped the loop but could retain callbacks belonging
to the previous tab. The final implementation fixes both sides:

1. `useGridClipboardActions` memoizes publication/copy/paste callbacks.
2. Stored actions read the current grid ref, not a captured handle snapshot.
3. Workspace publishers are stable and compare availability **and** callbacks.
4. Switching grids with equal availability still replaces their actions.

The export extraction deliberately uses an async event function rather than a
hook that publishes fresh callback objects into parent state.

Earlier debugging notes incorrectly attributed missing identifiers to import
ordering. ES module imports are hoisted; a top-level declaration before an
import is legal. A missing identifier requires checking the actual source,
scope and served bundle. Do not repeat import reordering as a runtime fix.

## Deliberately local

- Renovation filters, chunk scope, loaded-row patching, summary calculations,
  submit/approve and role-dependent editable fields.
- Raw-materials archive/fixing flow, per-section PATCH dispatch and user-delete
  guards.
- KPPK placeholder creation, row PATCH dispatch, realization period/slice rules.
- Renovation grid geometry, resize/header/scroll synchronization.
- `pendingCommitRef` serialization: moving it must not destabilize `commit`,
  a dependency of the grid's columns.
- Project-specific selection reset/preemption and renovation echo suppression.

## Verification of the final pass

- Frontend: TypeScript, oxlint/stylelint; 116 unit tests passed. New tests cover
  stable clipboard handlers/current refs/tab replacement, pending-write waiting,
  skip direction, applied/empty/conflicting history results and export cleanup.
- Backend: Ruff and ty passed; 390 tests passed, 4 skipped (local memory store).
- Authenticated browser: movement/prices, renovation plan/enterprises, all four
  KPPK tabs, realization slice change, fullscreen enter/exit, and a downloaded
  enterprises Excel file. No browser errors. Idle DOM measurement was zero
  mutations over two seconds on settled forms; the plan still incrementally
  rendered its loaded row window during measurement. This is not a performance
  benchmark or proof of zero React renders.
- Test caveats: the existing section-tabs test emits an `act(...)` warning.
  No production-scale workbook or concurrent-user load test was repeated here.

## Rules for future extractions

1. Preserve identities feeding grid columns and effects; test equality across
   rerenders, not just output values.
2. Prefer pure/event-only functions before state-owning hooks. Do not create
   effects whose only job is publishing fresh objects to parent state.
3. Never drop a new grid's handlers merely because its booleans equal the old
   grid's booleans. Stable callbacks and correct ownership are both required.
4. Keep project semantics local and coordinate changes in folders another agent
   owns. Never stash/reset a mixed working tree to diagnose a single component.
5. Check the actual authenticated workspace, mounted grid, console and tab
   transitions. A login screen without errors is not successful verification.
6. Typecheck, lint and unit tests do not replace runtime checks. Update this
   document and `docs/GRID.md` when shared contracts change.
