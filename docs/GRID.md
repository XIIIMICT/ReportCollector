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
- Row selection (click/shift/ctrl on the row number) **resets** the cell selection (`clearGridSelection`) — both state and the visual frame.
- A mouse-dragged selection is sent to presence **only on completion** (`setrange`), not live.

## Coordinates: sorting and filtering (the most important landmine)

Under sorting/filtering the screen row index **doesn't match** the `rows` index (the source array is unsorted). Anything mapping "index → row" must use **`getVisibleRows()`** (`grid.getVisibleSource("rgRow")` — the current sorted/filtered source): paste (building `models`), cut, copy (including the context menu), presence (my selected keys), `canPasteRange`. Otherwise values are committed to the wrong row → fake asset "deletions" → RESTRICT error (symptom: «ошибка удаления актива» when pasting on a sorted sheet).

## Presence and tooltips («В сети»)

- The «В сети» line in the workspace: one user → `<b>В сети:</b> только вы`; several → `4 в сети: Вы, Смирнов (админ), Кузнецов (СНХЗ, БСК)…` — last names, role «админ» (not «Администратор»), for an asset worker — the **concrete asset list**. Full FIO and cell ranges — only in the tooltip.
- **Tooltips — HeroUI `Tooltip`, not native** (for presence and busy cells; positioning reference — ReportCollector-RawMaterials). In the tooltip: full FIO as an unordered list, role/assets in parentheses, the cell range under the FIO in normal (not bold) font, the column name in quotes («Потребность / План, т»). `white-space: nowrap` + `max-width: none` — no word wrapping («Земелькин Сергей Александров\nич» is unacceptable).
- Yellow highlight of cells occupied by others (completed selection) with a "FIO — range" tooltip.
- In the archive «Движение» version presence isn't maintained — «В сети» isn't shown at all.

## Misc

- Changes apply live: `cell:changed` from the room updates rows (see `use-collab-room.ts`).
- Excel export — `POST /export` (openpyxl, see `export.ts` and `back/src/raw_materials/export.py`).
- Front unit tests: `front/src/shared/grid/__tests__/` (`grid-clipboard.test.ts`, `grid-export.test.ts`), run — `bun test` (happy-dom).
