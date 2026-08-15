# Grid (frontend, revo-grid)

Main code: `front/src/shared/grid/` (`raw-materials-grid.tsx` — the grid component, `grid-config.ts` — forms/columns config, `grid-edit.ts` — edits, `grid-editors.tsx` — cell editors, `section-tabs.tsx` — form tabs). Orchestration: `front/src/features/raw-materials/raw-materials-workspace.tsx`.

Stack: `@revolist/react-datagrid` 4.25 (revo-grid), React 19, HeroUI (tooltips, modals, toasts), Tailwind 4, Next 16. Custom editors are registered through `revo-grid` (see `grid-editors.tsx`).

## Forms and columns (`grid-config.ts`)

- `SectionId`: `materialMovement` («Движение»), `rawMaterialPrices` («Цены»), and the dictionaries `rawMaterials`, `assets`, `deliveryBases`, `users`. `MAIN_SECTIONS` = movement+prices; dictionaries are admin-only.
- `ColumnDescriptor`: key, label, width, kind (`text|number|boolean|asset|assetsMulti|basis|datetime|calculated`), `fixed` (pinned, non-editable), `path` (nested header, e.g. «Потребность / План, т»), `adminOnly`, `exportable`, placeholder flags.
- Merged headers (`ColumnGrouping`) are built from `path`; editors: `reference` (single pick from `source`), `multiAssets` (multi-pick), booleans — checkbox template.
- Each row is a `GridRow`: id, `rowState` (`unchanged|new|deleted`), `changedFields`, `isDeleted`, `canonicalCells` (canonical cell keys), `isPlaceholder` (the trailing «добавить запись» row).
- Default sort: raw-material items first, then assets; placeholder/deleted always at the end (`placeholderLastCellCompare`).
- Form reads: «Движение» — `GET /movement?report_month=`, «Цены» — `/prices`; dictionaries — their own GETs. Edits — `PATCH /movement`, `/prices`, `updateDictionaryCells`; one edit = a batch `{rowId, field, value}`.
- **«Бизнес-план, т» in «Движение» is a projection from «Цены», non-editable; in «Цены» business-plan edits go to `RawMaterialPrice` and are not "projected" onto other forms.**

## Clipboard

`raw-materials-grid.tsx`, "clipboard" section (functions `copySelection`, `pasteIntoSelection`, `cutSelection`, `buildCopyText`).

- **Copy (Ctrl+C)** — by value: numbers as-is (`,` for decimals), multi-dictionaries — joined with `,`; the copy goes to the system clipboard (`navigator.clipboard`).
- **Paste (Ctrl+V)** — the whole path goes through `rangeeditapply` → `beforeRangeEdit`, so locks/validation and undo/redo work uniformly:
  - Dictionary fields: single dictionary — the value must match an option; multi (`assetsMulti`) — `split(",")`, **each** value `str.strip()`-looked up among options, otherwise the paste is rejected (toast). When pasting dictionary rows, values are mapped to **existing UUIDs** (by value after strip).
  - Sizes: one cell selected → paste everything starting from that cell; ≥2 cells selected → paste only into the selected range: if the pasted range is bigger — it's **trimmed** by the range (toast), if smaller — pasted as-is, other cells untouched.
  - **Excel buffer**: trim trailing empty rows and columns (Excel puts a trailing `\n`) — otherwise an extra empty cell below overwrites a neighbor.
  - After paste the selection moves to the pasted cells and goes to presence.
- **Ctrl+X** — like Ctrl+C but clears the source cells (empty value; multi — empty list; boolean fields skipped). Through the same `rangeeditapply` → busy cells are skipped, the operation lands in undo/redo.
- **Hotkeys — by `event.code`** (`KeyC/KeyV/KeyX/KeyZ/KeyY`), otherwise broken with the Russian layout. Ctrl+Z/Y — undo/redo (server-side stack, see `docs/COLLAB.md`).
- The paste button («Вставить») only works when the buffer is compatible with the selection (`canPasteRange`) — also through the visible row source.

## Selection

- **Ctrl+A — fully custom**: a `document` handler in the capture phase with `stopImmediatePropagation` (kills revo-grid's built-in select-all, which draws three torn frames — header/data/bottom). Selects one contiguous range over the data cells, **without headers and without the «добавить запись» row**.
- **Programmatic selection — only through the grid's public API**: `grid.setCellsFocus(start, end)` + `grid.refresh()` (without `refresh` Stencil doesn't redraw the overlay). Reset — `grid.clearFocus()` + `grid.refresh()`: the store empties, but without `refresh` the old range rectangle stays visible. **`store.setRange` doesn't exist** (in `revogr-overlay-selection` there's a raw observable without methods) — it's dead code, don't use it.
- Row selection (click/shift/ctrl on the row number) **resets** the cell selection (`clearGridSelection`) — both state and the visual frame. **Order matters**: the range is cleared **before** `setSelectedRowIds` — the row selection rebuilds `columns` (selectedRowIds is a `columns` dependency) and revo-grid's `columnChanged` captures the still-live focus into `pendingColumnFocusRestore`, which `restoreColumnFocusAfterRender` then re-applies as a single-cell range (its guard can't match an empty store). `clearGridSelection` also drops the stale restore for the same reason.
- A mouse-dragged selection is sent to presence **only on completion** (`setrange`), not live.

## Pinned header and scrollbars

- The «Закрепить заголовок» option (grid options menu, ⋮) is **on by default** (`headerPinned` state in `raw-materials-workspace.tsx`). Pinned mode: the grid fills the shell (`revo-grid-shell--pinned`), revo-grid scrolls internally (sticky header stays visible), its own virtual scrollbars (`revogr-scroll-virtual.vertical/.horizontal`) drive the viewport. Unpinned: an external vertical scroller plus a custom horizontal strip (`revo-grid-external-scrollbar`), the grid is sized to its content (`--grid-content-height`).
- **Scrollbars are the browser's native ones, no custom styling.** revo-grid hides its raw viewport scrollbars (`scrollbar-width: none`) and renders `revogr-scroll-virtual` strips whose *own* native scrollbars are the visible bars — they're real light-DOM elements, and revo-grid sizes them to the measured system scrollbar width, so pinned and unpinned bars naturally match. **Landmine**: revo-grid's light theme sets `color-scheme: light` on the host → native scrollbars render light even in the app's dark theme; `globals.css` fixes it with `.dark .raw-materials-grid { color-scheme: dark }` — keep that override if the `theme` prop changes.
- Scrollbars appear only when there's overflow (native `overflow: auto`); `revogr-scroll-virtual` collapses to 0 size without overflow (revo-grid sets its min-size to 0 in `componentDidRender`). In unpinned mode the external strip is hidden when the content fits (`revo-grid-shell--no-hscroll`, `hasHorizontalOverflow` state) — no dead 17px gap under the last row.
- **In unpinned mode BOTH internal scrollbars are hidden** (`revogr-scroll-virtual`, not just `.horizontal`) — the external scroller and the external horizontal strip own the scrollbars there. Without hiding the internal vertical one, its native scrollbar lingers next to the external bar right after unpinning (its spacer keeps the old height until revo-grid's async re-measure) — the "duplicated scrollbar" flicker. **Landmine**: hiding is done by zeroing the strip sizes (`width/height: 0 !important` + `min-width/min-height: 0 !important` in `globals.css`), **not** by `visibility: hidden` alone — revo-grid sets an inline `min-width/min-height` on the strip hosts (which would win), and Firefox paints the native scrollbar of a `visibility: hidden` scroll container (the "dead strip" after unpinning). A zero-size strip has no scrollbar to paint in any browser.
- **Wheel over the horizontal scrollbar scrolls it horizontally** (native Chrome behavior scrolls the vertical axis instead). A native `wheel` listener on the shell (`{ passive: false }` — React's `onWheel` is passive and can't `preventDefault`) detects the cursor over `revogr-scroll-virtual.horizontal` (pinned) or the external strip (unpinned) and translates `deltaY + deltaX` into `scrollLeft`; the strips proxy to the viewport via revo-grid's `scrollvirtual` events.

## Coordinates: sorting and filtering (the most important landmine)

Under sorting/filtering the screen row index **doesn't match** the `rows` index (the source array is unsorted). Anything mapping "index → row" must use **`getVisibleRows()`** (`grid.getVisibleSource("rgRow")` — the current sorted/filtered source): paste (building `models`), cut, copy (including the context menu), presence (my selected keys), `canPasteRange`. Otherwise values are committed to the wrong row → fake asset "deletions" → RESTRICT error (symptom: «ошибка удаления актива» when pasting on a sorted sheet).

## Presence and tooltips («На форме»)

- The «На форме» line in the workspace: one user → `<b>На форме:</b> только вы`; several → `<b>На форме:</b> Вы, Смирнов (админ), Кузнецов (СНХЗ, БСК)…` — last names, role «админ» (not «Администратор»), for an asset worker — the **concrete asset list**. Full FIO and cell ranges — only in the tooltip.
- **Tooltips — HeroUI `Tooltip`, not native** (for presence and busy cells; positioning reference — ReportCollector-RawMaterials). In the tooltip: full FIO as an unordered list, role/assets in parentheses, the cell range under the FIO in normal (not bold) font, the column name in quotes («Потребность / План, т»). `white-space: nowrap` + `max-width: none` — no word wrapping («Земелькин Сергей Александров\nич» is unacceptable).
- Yellow highlight of cells occupied by others (completed selection) with a "FIO — range" tooltip.
- In the archive «Движение» version presence isn't maintained — «На форме» isn't shown at all.

## Footer stats block

- The footer shows three lines (`.grid-stats`, column layout): **«Выделено: …»** (the current selection in the same format as the «На форме» tooltip — e.g. «Потребность / План, т», строки 3–5; row selection → «строки 3–5»; hidden when nothing is selected), **«Строки: X на экране, Y всего»** (X = rows matching the current filters, not the viewport count — `getVisibleSource` after filtering; Y = total non-placeholder rows), **«Столбцы: k неизменяемых + n расчётных + x заполняемых = … всего»** (from `ColumnDescriptor`: `fixed` non-`calculated` / `kind === "calculated"` / the rest).
- Stats come from `GridViewStats` (grid → `onViewStatsChange`): `totalRows`, `filteredRows`, `totalCols`, `immutableCols`, `calculatedCols`, `editableCols`. Re-measured on `aftersourceset`/`viewportscroll`/`columnresize`, and on `beforefilterapply` (setTimeout 0 — revo-grid has no public after-filter event; filtering trims the data store synchronously). The selection label is the `onSelectionChange` payload (already sent to presence), stored in the workspace.

## Misc

- Changes apply live: `cell:changed` from the room updates rows (see `use-collab-room.ts`).
- Excel export — `POST /export` (openpyxl, see `export.ts` and `back/src/raw_materials/export.py`).
- Front unit tests: `front/src/shared/grid/__tests__/` (`grid-clipboard.test.ts`, `grid-export.test.ts`), run — `bun test` (happy-dom).
