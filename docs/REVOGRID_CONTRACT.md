# RevoGrid contract

This document records the RevoGrid surface used by the frontend. The
application treats the installed package combination as one compatibility
matrix; changing one package requires the adapter tests and the browser smoke
to pass again.

## Installed matrix

| Package | Declared/installed version |
| --- | --- |
| `next` | `16.4.6` |
| `react` / `react-dom` | `19.3.1` |
| `@revolist/react-datagrid` | `4.32.0` |
| `@revolist/revogrid` | `4.32.0` via the workspace override |
| `@revolist/rv-pro-trial` | `3.0.18` |
| `InfinityScrollPlugin` | exported by `@revolist/rv-pro-trial` |

The wrapper and core versions are intentionally recorded separately because
the override installs RevoGrid `4.32.0` on its own. The override is a
compatibility decision, not evidence that the versions are interchangeable. Do
not upgrade either side without updating this table and running the
contract/browser checks.

## Application-owned adapter

`front/src/shared/grid/revo-grid-adapter.ts` is the only module that owns the
RevoGrid integration details shared by the in-memory and sparse grids. It
provides:

- visible and sparse source reads with a disposed-grid fallback;
- range set/clear commands, including the refresh required for the selection
  overlay to repaint;
- selected-range and coordinate-scroll commands;
- composed `rangeeditapply` dispatch;
- shadow-DOM-aware event target checks;
- the temporary drag-range subscription used by the autofill overlay;
- sparse-range coverage checks and a monotonically increasing scope epoch.

The adapter contains the only access to the private
`pendingColumnFocusRestore` field and the internal
`revogr-temp-range.selectionStore`. Feature components must use adapter
methods instead of reading those implementation details directly.

## Event shapes relied upon

The current grids rely on these public root events from the installed
RevoGrid types:

| Event | Use |
| --- | --- |
| `afteredit` | single-cell commit |
| `beforerangeedit` | validate and normalize a range commit |
| `setrange` | publish a completed range selection |
| `focuscell` | update the active cell/range while dragging |
| `afterfocus` | report the selected row position in the sparse plan |
| `canceledit` | release the active collaboration lease |
| `viewportscroll` / `scrollchange` | geometry and sparse-source lifecycle |
| `beforesorting` / `aftersortingapply` | client-grid sorting policy |
| `aftersourceset` | source lifecycle notification |

The sparse plan also uses the Pro `InfinityScrollPlugin` source contract:
chunks are loaded by offset/limit and unloaded rows are represented by holes.
The plan therefore rejects copy/paste ranges with missing loaded rows instead
of silently emitting a partial operation.

## Supported runtime checks

The repository currently runs the smoke project with Playwright Desktop
Chrome and `ru-RU` locale. A broader supported-browser matrix has not yet been
approved. The adapter contract suite is the fast check for public method and
event assumptions; the browser smoke remains required for shadow-DOM focus,
native clipboard, sorting and InfinityScroll behaviour.
