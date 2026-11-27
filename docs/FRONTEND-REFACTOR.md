# Frontend refactor: baseline and progress

Scope: ReportCollector `front/` only. DocMasker is owned by another agent.
Every measurement below was taken from the checked-out `dev`-derived task
branch; no numbers are estimated.

## Environment and baseline (2026-11-26)

- Windows 11, Bun 1.4.4, Next.js 16.4.6 (Turbopack), React 19.3.1,
  HeroUI 3.2.8, Tailwind 4.3.5, RevoGrid 4.32.0.
- Commands from `front/package.json`: `bun run format:check`,
  `bun run lint:check`, `bun run typecheck`, `bun run test`, `bun run build`.

| Check | Baseline result |
|---|---|
| `format:check` | pass (259 files) |
| `lint:check` | pass (existing `react-perf` warnings in `renovation-grid.tsx`, no errors) |
| `typecheck` | pass (253 files) |
| `test` | 466 pass / 0 fail / 1201 assertions (80 files) |
| `build` | compiled in 12.7 s, TypeScript 3.1 s, 4 static pages; 16 routes |

Client bundle baseline (`.next/static/chunks/*.js`, uncompressed / gzip):
**3,722.9 KB / 1,198.2 KB across 37 chunks**; largest 1,497.8 KB, 653.8 KB,
202.4 KB, 163.7 KB, 132.0 KB. That is the 2026-11-26 HISTORICAL figure; the
Phase-0 re-measurement and the route breakdown are below.

### Bundle analyzer (Turbopack, 2026-11-27)

`bun run analyze` uses the Turbopack analyzer shipped with the installed Next
16.4.6: `next analyze --output --snapshot reportcollector` writes a named
snapshot and UI files to `.next/diagnostics/analyze`, then
`next analyze export --snapshot reportcollector` streams the graph as JSON Lines
to `.next/diagnostics/analyze/graph.jsonl` (schema v1: 1 meta / 3123 module /
17 route / 2144 output / 10430 part / 109 group records). Run `next analyze`
without `--output` to browse the saved snapshot interactively. The output lives
under `.next/`, which `front/.gitignore` excludes with its deny-all rule — no
analyzer artifact is committed.

The former setup (`ANALYZE=true next build` plus `@next/bundle-analyzer` in
`next.config.ts`) was removed: the installed plugin bails on
`process.env.TURBOPACK` with *"Next Bundle Analyzer is not compatible with
Turbopack builds, no report will be generated"*, so it never registered a report
hook for the real (Turbopack) build, exited 0 and produced no artifact. The
dependency and its webpack-only transitive packages were removed with it. The
CLI was re-verified from `node_modules/next/dist/bin/next` and the shipped
`next.md` docs before the change.

Compact before/after of the recorded bundle numbers (source:
`docs/FRONTEND-AUDIT-BASELINE.md` §3.2–3.3; gzip via .NET `GZipStream` at
`Optimal`, so a different compressor shifts the gzip column slightly). The
analyzer swap itself does not change the bundle — these are the Phase-0
measurements kept here as the reference, not a claim of optimization:

| Route | JS files | JS raw (KiB) | JS gzip (KiB) | CSS raw (KiB) | CSS gzip (KiB) |
|---|---:|---:|---:|---:|---:|
| `/` | 11 | 631.8 | 194.2 | 532.3 | 54.8 |
| `/_not-found` | 11 | 631.8 | 194.2 | 532.3 | 54.8 |
| `/[project]` (workspace) | 13 | **776.9** | **241.5** | 532.3 | 54.8 |
| `/blocked` | 12 | 636.0 | 195.5 | 532.3 | 54.8 |
| 12 `/api/*` routes | 0 | 0 | 0 | 0 | 0 |

Aggregate emitted client JS: **before** (2026-11-26) 3,722.9 KB / 1,198.2 KB
gzip → **after** (Phase 0, 2026-11-27) 3,723.7 KiB / 1,186.5 KiB gzip, 37 chunks
in both. Raw matches within noise; the ~11.7 KiB gzip gap is a compressor
difference, not a regression. The two largest chunks (1,497.8 / 655.3 KiB raw)
are async workspace/grid chunks, not initial-route payload. A third CSS chunk
(`12rjs38kcanz9.css`, 363,722 B raw / 53,599 B gzip) is the RevoGrid Pro trial
stylesheet loaded with grid sections, outside the base route CSS list.

Runtime scenarios from the audit (94k-row plan, paste/copy, collaboration) were
**not measured**: they need the prepared full stack (Postgres + Valkey + seed,
`KEYCLOAK_ENABLED=false`). Playwright/e2e is therefore not part of this stage.
No performance claim is made until those measurements exist.

## Stage 6 — duplicated pure helpers (done)

Removed exact/equivalent local copies and kept domain-specific logic explicit:

- `raw-materials-workspace.tsx` and `renovation-dictionary-section.tsx`: local
  `toGridRow` replaced with `toGridRow` from `shared/ui/text-helpers.ts`.
- `kppk-workspace.tsx`: local `draftText`, `recordLabel` and `errorMessage`
  replaced with `trimmedText`, `recordLabel(row, ["name", "code"])` and
  `errorMessage`. Its `toGridRow` now composes the shared envelope with the
  KPPK-only `orgShortNames → orgNamesDisplay` display mapping.
- `kppk-realization-section.tsx`: local `errorMessage` replaced with the shared one.
- `renovation-workspace.tsx`'s `toGridRow(RenovationPlanRow, diffMap)` is
  deliberately kept: its diff/version semantics are domain-specific.

Unit tests added to `shared/ui/__tests__/text-helpers.test.ts` for the row
envelope (including no source mutation), caller-specific label candidate order,
and the Russian error fallback. After the change: 469 pass / 0 fail;
format/lint/typecheck green. A scan for other byte-identical function bodies in
`front/src/features/**` found none.

Production build after the change: compiled in 2.9 s, TypeScript 3.8 s.
Bundle: **3,722.5 KB / 1,198.1 KB gzip across 37 chunks** (baseline 3,722.9 /
1,198.2 KB) — the dedup is a maintainability change, not a bundle optimization,
so no performance gain is claimed.

## Stages 5.2–5.3 — first safe extractions (done)

- `renovation-grid-edits.ts`: the plan grid's `extractEdits` event adapter is now
  a pure module (with `renovationCellKind`) and has 6 unit tests for the
  single-cell/`data`-snapshot shape, range patches, missing models, deleted rows,
  unchanged values and deduplication. `renovation-grid.tsx` went from
  **4,181 → 4,116 lines**; no RevoGrid API, ref or callback identity changed.
- `renovation-workspace-view.ts`: pure scope-key, Russian validation-column
  regex, plan-row → grid-row mapping, version timestamp and block labels moved
  out of the workspace with 4 unit tests. `renovation-workspace.tsx` went from
  **2,282 → 2,192 lines**; ownership of state stays in the workspace.
- Tests after both extractions: **479 pass / 0 fail**; format, lint, typecheck
  and production build green. Build: compiled 3.6 s, TypeScript 1.5 s; bundle
  **3,722.3 KB / 1,198.1 KB gzip / 37 chunks** — still noise-level, no
  performance claim.

## Stage 5.2 — workspace pure selectors (first safe step, done)

- New `front/src/features/renovation/renovation-workspace-selectors.ts`: eight
  pure derivations (four groups) lifted verbatim out of `renovation-workspace.tsx`
  — version-picker `buildArchiveItems`, `renovationSections` +
  `sectionTabDescriptors`, the editable/calculated/mergeable field-key set
  builders, and `renovationAssetNames` + `planAssetFilterSet`.
- The workspace remains the composition root and sole owner of React state:
  every `useMemo`/`useCallback` wrapper and its dependency list is unchanged,
  only the memo bodies became calls. No new store, no props/context changes.
  `renovation-workspace.tsx`: 2,192 → 2,145 lines.
- 11 unit tests in `__tests__/renovation-workspace-selectors.test.ts` cover the
  empty/missing-data cases, block/approver rules, the approve-response override
  and input immutability. Full suite: **492 pass / 0 fail / 1,276 assertions
  (83 files)**; `format`, `format:check`, `lint:check`, `typecheck` and `test`
  all exit 0. Production build and Playwright are intentionally left to the
  parent stage after integration.

## Stage 5.3 — renovation grid pure extractions (done)

`renovation-grid.tsx` keeps its React tree, refs, memoized structures and every
dependency list; only pure factory bodies were lifted into three new modules:
`renovation-grid-geometry.ts` (level offsets, column left edges),
`renovation-grid-header-chain.ts` (chain/level derivations from header metadata)
and `renovation-grid-value-normalization.ts` (cell value coercion + workbook
rules). Call sites in `beforeEdit`/`beforeRangeEdit` and the header memos now
delegate to them. All RevoGrid selection/geometry/clipboard invariants are
untouched; unit tests cover empty data, boundaries and 217-column inputs.

## End-to-end smoke selection and verification (2026-11-27)

The smoke suite is defined in `front/playwright.config.ts` — the single
Playwright config, shared by local and CI runs. CI runs the browser smoke
against its isolated production image at `E2E_BASE_URL=http://127.0.0.1:15000`;
the local CI-like stand used for repeat runs is `http://127.0.0.1:16000`, both
with `KEYCLOAK_ENABLED=false`. The separate `ui-keycloak` project needs an
independently prepared IdP and is not started by CI.

`bunx --no-install playwright test --project=smoke --list` currently discovers
44 tests in 10 spec files. CI checks the required spec basenames, not an exact
count, so this number is a snapshot rather than a contract. The earlier runs in
this section used a prebuilt production image and reported "30 passed / 1
failed / 3 skipped"; that failure predates the refactor and those counts were
never evidence for it, so they are removed rather than restated as current.

No browser DOM/row-count, FPS or latency baseline has been recorded on a
prepared stack; no retroactive runtime-performance claim is made for the
structural refactor stages above.

## Stage 8.1 — Tailwind breakpoints (done)

Verification before changing anything: HeroUI 3.2.8's compiled styles use
literal `@media (min-width:40rem)` / `48rem` rules, so overriding
`--breakpoint-sm/md` to `0rem` never affected its component scaling. The only
effect was on Tailwind utilities: `sm:max-w-[420px]` in the Excel export dialog
compiled to `@media (min-width:0){...}`, applying at every width.

- Removed the two `@theme` overrides; `sm` / `md` keep their standard values.
- Replaced the dialog's `sm:max-w-[420px]` with unconditional `max-w-[420px]`
  so its rendered width is unchanged on narrow screens.
- Source scan: no remaining `sm:` / `md:` utilities in `front/src`.
- Built CSS verification: no `.sm\:max-w-[420px]` rule; `.max-w-\[420px\]{max-width:420px}`
  is unconditional. Bundle stays 3,722.3 KB / 1,198.1 KB gzip / 37 chunks.

This is a CSS-semantics fix, not a visual change; no screenshot claim is made.

## Stage 9 — CSS split (done)

`globals.css` is now the App Router entry point only: `@import "tailwindcss";`,
`@import "@heroui/styles";`, then 11 imports from `./styles/` in this exact
cascade order (the partials are contiguous slices of the former 4,250-line
file — no selector or declaration was reordered or rewritten):

1. `01-theme-and-base.css` — tokens, reset, `html` / `body`
2. `02-app-shell-and-header.css` — shell, header, brand, profile, support link
3. `03-workspace-and-toolbar.css` — workspace, toolbar, tabs, grid viewport
4. `04-dialogs-and-view-strip.css` — row/record/import dialogs, view strip, alerts
5. `05-form-status-and-footer.css` — status badge/tooltip, footer, collapse controls
6. `06-action-bar-fullscreen-and-misc.css` — action bar, expandable buttons, fullscreen, profile modal
7. `07-grid-common.css` — shared RevoGrid shell, cells, headers, editors
8. `08-renovation-grid.css` — plan grid, overlay header, dictionaries
9. `09-responsive-and-grid-controls.css` — `<lg` block, grid editors, `sm` logo block
10. `10-header-filters-and-overlays.css` — funnel, hidden columns, filter popovers, go-to
11. `11-reduced-motion.css` — the consolidated reduced-motion policy, imported **last**

The five pre-existing `prefers-reduced-motion` blocks stay at their original
positions inside their owning partials: hoisting them into the final partial
would change which rule wins. Verification: concatenated partials are
whitespace-normalised identical to the old rule stream (136,919 bytes after
`oxfmt`; 477 rule blocks, 635 selectors, 1,673 declarations, 89 `!important`,
15 `@media` blocks including 6 reduced-motion). The two emitted production CSS
chunks are byte-identical (matching SHA-256) before and after the split. No
visual/e2e claim is made here — the parent stage runs that against the stack.

## Zustand decision

**Not needed.** Panel filters remain URL-backed per tab
(`use-toolbar-filters.ts`), grid filters/drafts/sorting remain scoped by
project and section in localStorage (`use-grid-column-filters.ts`), transient
state stays local, and RevoGrid owns its own interaction state. No measured
rerender or cross-branch state problem has been demonstrated yet; adding a
store would multiply canonical sources without fixing one.

## Stage 8.2 — reduced motion (done)

Audited the five existing `prefers-reduced-motion` blocks and the 34 animated
app-owned CSS blocks. Added one consolidated block at the end of `globals.css`
covering the previously unprotected surfaces (header, toolbar, footer,
fullscreen panel, grid header overlays/filters, status dots, popover entrance
animations). HeroUI's own reduced-motion policy is not overridden. An e2e case
uses `page.emulateMedia({ reducedMotion: "reduce" })` and asserts computed
`transition-duration: 0s` on `.app-header`.

Duration/easing token consolidation (the audit's §8.2.5) is intentionally left
for the CSS-split stage: the stylesheet still uses ~15 ad-hoc timing values,
and replacing them mechanically would be a visual-risk change without a
measurement. No animation claim is made.

## Stage 8.3 — HoverDropdown (done elsewhere)

The 60 ms polling loop was replaced with React Aria `onHoverStart`, pointermove
coordinate tracking, one RAF-coalesced geometry check and one cancellable
250 ms timeout. See `front/e2e/hover-dropdown.spec.ts` and `docs/GRID.md`.

## Stage 5.4 — proportional review of other workspaces (done)

Reviewed `raw-materials`, `kppk` and `logistics` workspaces for genuine
repetition. Most lifecycle machinery was already shared (`ScopedMutationQueue`,
history helpers, `useTableExport`, `useGridClipboardActions`, column filters,
remote-cell maps). Extracted only mechanisms with proven identical contracts:
`buildGridResetState` (6 call sites), `ScopeStaleNotice` (3 sites) and
`sameGridViewStats` (KPPK + logistics). Everything else was deliberately left
explicit — similar-looking undo/remote-cell/clipboard paths have different
semantics, and wrapping two-line idioms in hooks would add indirection without
removing logic. 12 new deterministic tests (534 unit tests total).

## Stage B — shared clipboard I/O (2026-11-26)

The four repeated `navigator.clipboard` write/read + textarea/`execCommand`
fallback blocks in `data-grid.tsx` (copy, cut, paste-read) and
`renovation-grid.tsx` (copy, paste-read) now call the existing
`grid-clipboard.ts` helpers. Cut still aborts before clearing source cells when
both write paths fail; the fallback textarea is removed in `finally` so a
throwing `execCommand` cannot leak it. Empty clipboard reads normalize to the
same `null` sentinel as failed reads. Row-source mapping, sparse-row checks,
paste parsing, selection restore, RevoGrid payloads and all memo dependencies
remain local and unchanged; 8 new unit tests (542 total).

## Stage C — neutral grid contracts (2026-11-26)

`SUMMARY_ROW_ID` and `GridViewStats` moved out of the `data-grid.tsx`
implementation into `shared/grid/grid-constants.ts` and `grid-types.ts`; both
are re-exported from the `shared/grid` barrel. All thirteen import sites were
updated to the neutral modules, and neither name is imported from `data-grid`
any more — RenovationGrid no longer depends on the other grid's implementation
for common contracts. No re-export from `data-grid` was added, so the old
dependency cannot silently return. Values, comments, memo dependencies and JSX
are unchanged; 544 unit tests.

### Stage C (cont.) — pure config boundaries (2026-11-26)

`grid-config.ts` dropped from 1,378 to 979 lines. The eight shared type
declarations moved to `grid-config-types.ts`; the twelve pure number/date/facet
formatters, placeholder-last comparator and `exportFileName` (plus
`SECTION_LABELS`, needed by the latter) moved to `grid-format.ts`. The original
module re-exports all of them, so roughly thirty existing import sites are
untouched; `grid-format.ts` has no runtime import of `grid-config.ts`, so no
cycle was introduced. 15 new formatter/re-export tests (559 total).
Descriptors, column builders, cell templates and cell-property logic stayed in
`grid-config.ts` — they carry RevoGrid callback identities and are not pure.

## Test quality — coverage gate (2026-11-26)

`scripts/coverage-gate.ts` is now a thin CLI wrapper over the pure
`coverage-gate-parse.ts`: `--min 65` and `--min=65` share one validator, LF/LH
records are strict non-negative integers with `LH <= LF`, CRLF is accepted and
all file records are aggregated. Malformed thresholds/reports and `NaN`
percentages fail with a clear error instead of a false pass. The 65% floor is
unchanged (fresh baseline 73.94%). 621 unit tests; the parser module was also
whitelisted in `front/.dockerignore` so an image running the gate has it.

## Grid decomposition — Stage 1: shared skipped-cell reason policy (2026-11-26)

The byte-identical Russian message construction from a `Map<label, reason>`
inside both grids' `beforeRangeEdit` is now one pure
`shared/grid/skipped-cell-reasons.ts::groupSkippedCells`, with 9 equivalence
tests (630 unit tests total). Domain-specific policy — descriptor/field lookup,
`canEditDescriptor` vs `cellAllowed`, canonical keys, sparse rows and
normalization — remains in the owning grid; no `isRenovation` flag, new hook or
dependency was introduced. This is the foundation for the deeper stateful
decomposition, not a substitute for it.

## Grid decomposition — Stage 2: DataGrid edit decisions (2026-11-26)

`data-grid.tsx` dropped another ~100 lines: `checkEditStart`, `checkEdit`,
`uneditableReason` and `normalizeRangePatch` now live in the pure
`shared/grid/data-grid-editing.ts` with 26 tests (656 unit tests total). Only
decision logic moved; `preventDefault`, lease refs/acquire, validation toasts,
event payload casts, `afterEdit`, memo dependencies and JSX remain in the
component. The own-lease-beats-foreign-presence asymmetry and the invalid-value
early return before skipped-cell messages are pinned by tests.

## Grid decomposition — Stage 3: selection helper contract (2026-11-26)

The unused `grid-selection.ts::selectionLabelForRange` (different format and no
call sites) was removed, leaving `selection-label.ts` as the single public
naming contract. `selectionCellKeys` / `selectionRowCellKeys` now accept an
explicit `getCellKey` accessor (default keeps the old `id/prop` keys);
DataGrid's two local canonical-key copies were replaced with calls and a small
accessor at the call boundary. 9 new tests (665 total); selection publication,
effect dependencies and all RevoGrid invariants unchanged.

## Grid decomposition — Stage 4: descriptor data (2026-11-26)

`grid-config.ts` dropped from 979 to 587 lines. All section metadata, the
`DESCRIPTORS` table, KPPK/renovation/service descriptor sets, the six descriptor
factories and `getVisibleDescriptors` / `getEditableKeys` moved verbatim into
`grid-config-descriptors.ts`; the original module re-exports all 16 names, and
the new module has no import of it, so no cycle exists. 16 new tests (681
total). Column builders, cell templates, cell-property logic and export-column
construction stayed in `grid-config.ts` because they carry RevoGrid callback
identities.

## Grid decomposition — Stage 5: DataGrid scroll restore (2026-11-26)

The whole remembered-position lifecycle (four refs, two restore effects, the
bounded 20-attempt retry, the 250 ms debounced save, its unmount cleanup and
the `scrollchange` listener) moved into `use-grid-scroll-restore.ts`. The hook
takes six explicit arguments and returns only the one debounce trigger that the
unchanged JSX needs; all timeout values, tolerance math, scope re-arm semantics
and dependency arrays are preserved. 8 hook tests (689 unit tests total).

## Grid decomposition — Stage 6: cell rendering (2026-11-26)

The final pure slice of `grid-config.ts`: `isCellReadOnly`, `cellProperties`,
`nextBooleanValue`, `renderedDisplayValue` and all six cell templates moved
verbatim to `grid-cell-rendering.tsx` (249 lines). The module imports only types
and formatters, so the dependency stays one-way and all 10 names are
re-exported from `grid-config.ts` (732 unit tests after 43 new cases).
`grid-config.ts` is now 360 lines, holding only column/export builders.

## Phase 2.2/2.4 — grid hot-path algorithms (2026-11-27, STATIC)

**BLOCKED: runtime profiling was not possible here.** The documented ~94k-row
plan fixture does not exist locally and the disposable DB has only **4 plan
rows / 67 asset rows / 134 movement rows** (`docs/FRONTEND-AUDIT-BASELINE.md`
§4); the 306k-row harness is a backend-only window benchmark. No browser trace,
long-task/INP recording, FPS sample, React Profiler session or heap snapshot
was taken, and no large-plan E2E was run. Everything labelled STATIC below is a
source-reading conclusion about asymptotic behaviour — **not** a measured
runtime gain. MEASURED means the exact check output at the end of the section.

### Ownership and hot-path map (all rows STATIC unless tagged)

| Operation | Owner / lines | Input size | Complexity before → after | Tag |
|---|---|---|---|---|
| Single-cell / range edit extraction | `grid-edit.ts:12-73`, `renovation-grid-edits.ts:44-78`, called at `data-grid.tsx` `afterEdit` and `renovation-grid.tsx:2383` | patch cells `P` (+ row models) | `O(P)` time / `O(P)` space; `previousValues` is already a keyed map, patches are iterated once | STATIC, unchanged |
| Paste / cut range build | `data-grid.tsx:2872-3043`, `renovation-grid.tsx:2512-2589`, `grid-clipboard.ts:23-47` | Excel buffer `B`, target `H×W`, loaded window `Wv` | `O(B + H·W)` time / `O(H·W)` space for `data`/`models`; the plan resolves rows through `rowsByIndex` (`renovation-grid.tsx:1220-1226`). No JSON round-trip or deep clone in the paste path | STATIC, unchanged |
| Copy with row-header selection | `data-grid.tsx:2877-2911` | selected rows `S`, source rows `N`, columns `C` | before `O(S·N)` (`rows.find` per id) → after `O(N + S)` keyed pass; space `O(N)` for the id map | STATIC, **changed** |
| Selection publication / presence keys | `data-grid.tsx:1337-1411`, `renovation-grid.tsx:2795-2936` | visible/loaded rows `N`, selected `S`, columns `C` | `O(N + S·C)` and `O(R·C)` for the published range; debounced and bounded by visible/loaded rows | STATIC, unchanged |
| Sort / filter / facets | `data-grid.tsx:895-935`, `facetSourceFor` `data-grid.tsx:901-923`, `column-filter-facets.ts:356-402` | source rows `N` | one `O(N)` pass per filter/facet derivation; `computeRowFacets` is already single-pass. The plan's facets are server-side (`renovation-column-filter.tsx:101`) | STATIC, unchanged |
| Remote cell apply (`cell:changed`) | `renovation-workspace.tsx:1064-1086` | loaded rows `W`, mergeable fields `F≈217`, received values `R` | before `O(W·F)` **per frame** → after `O(R + W)` with a keyed inverse index; same patch order and the same replay of pending values to newly loaded rows | STATIC, **changed** |
| Remote cell overlay (client grids) | `raw-materials-workspace.tsx:669-701` and the KPPK/logistics mirrors | `N` × editable fields `F` on every `remoteCellValues` change | `O(N·F)`, but these grids hold tens/low hundreds of rows (MEASURED fixture counts: 67 assets, 134 movements); no large-workspace evidence | STATIC, rejected |
| Data replacement / previous-value snapshot | `data-grid.tsx:498`, `renovation-grid.tsx:1693` | all rows `N` / loaded window `W` | one `O(N)` / `O(W)` keyed map with a shallow row clone **per rows change**, not per edit; the clone is required because the plan patches rows in place | STATIC, rejected |
| Pinned-row patch reconciliation | `renovation-grid.tsx:3221-3240` | pinned snapshots `P`, patches `R` | `O(P·R)`; `P` is 0 in the common case and the effect returns early | STATIC, rejected |
| Remembered-position lookup | `renovation-grid.tsx:2104` | sparse source length `N` | `O(N)` scan, but only on `afterFocus` (a deliberate click), not on scroll | STATIC, rejected |

All index→row operations continue to go through `getVisibleRows()` /
`rowsByIndex` under sorting and filtering (`front/AGENTS.md` landmine 2,
`docs/GRID.md` «Coordinates»). No callback identity, memo dependency,
RevoGrid event ordering, imperative handle, selection restore, undo/rollback,
paste ordering, collaboration seq/delta/lease or keyboard contract was changed.

### Chosen fixes

1. **Keyed row resolution for id-based copy** — `grid-clipboard.ts`
   `rowsForSelectedIds`, called from `data-grid.tsx:2887`. `Array.find` per
   selected id was quadratic in a large row-header selection; the map keeps the
   first row for duplicate ids (what `find` returned) and skips unknown ids, so
   emitted TSV line order is unchanged. Covers copy and cut (cut reuses
   `buildCopyText`).
2. **Inverted remote-cell index** — `renovation-workspace-view.ts`
   `remoteCellPatchesByRow`, called at `renovation-workspace.tsx:1075`. The old
   effect scanned every loaded row × every mergeable field for each frame; the
   new helper walks only received `table/rowId/field` keys and groups them by
   row, then the caller resolves them through the existing `loadedRowsRef`
   keyed mirror. Foreign-table keys, malformed keys, non-mergeable fields and
   `undefined` values are ignored; `null` is still applied; the
   `work_or_repair_code` → `__renovationId` / `__missingComponents` derivation
   is unchanged. Walking the loaded rows afterwards preserves patch order and
   re-applies pending values to rows that only just loaded.

Tests: 5 cases for the keyed row selection (empty/sparse rows, duplicate ids,
selection order, a 1000×5000 repeated-selection property) in
`grid-clipboard.test.ts`; 6 cases for the inverted remote index (empty map,
grouping with `null`, work-id companions, foreign/malformed/stale keys, explicit
table isolation, a 500-change accumulation) in
`renovation-workspace-view.test.ts`. Existing clipboard/paste, selection,
collab and undo tests were not weakened.

### Rejected alternatives (with evidence)

- Memoizing `computeRowFacets`, `extractEdits` or column builders globally:
  they are already single-pass or patch-sized, React Compiler is on, and no
  profile attributes cost to them.
- Reusing `rowValuesRef` for copy: it is a snapshot taken at a rows-change
  boundary; copy must read current `rows` (a change in semantics), so the helper
  builds a reference-only map at call time instead.
- Inverting the raw-materials grid-row mirror too: O(N·F) but N is 67/134 in
  the only measured fixture; no evidence of a large client-grid workload.
- Rewriting the pinned-row `rowPatches.find` and the scroll-restore `find`:
  both run on rare/small inputs (pinned rows are usually none; the lookup runs
  on a click), so they are micro-optimizations without a measurement.
- Web Worker for paste normalization, a generic grid framework, a global store
  or unifying the two grids: no measurement showing main-thread blocking, and
  each would change the RevoGrid/domain contracts documented in `docs/GRID.md`.

### Validation (MEASURED)

| Command | Exit | Result |
|---|---:|---|
| `bun run --cwd front format` then `format:check` | 0 | oxfmt: 307 files correct |
| `bun run --cwd front lint:check` | 0 | 0 errors, 683 warnings (unchanged from the Phase-0 baseline; all oxlint `react-perf`/deps/`no-shadow`) |
| `bun run --cwd front typecheck` | 0 | `bun check`: no type errors in 290 files |
| `bun run --cwd front test` | 0 | **746 pass / 0 fail / 2175 assertions / 96 files** (baseline 735; +11 new tests) |
| `bun run --cwd front build` | 0 | Next 16.4.6 Turbopack; compiled 1831 ms, TypeScript 1031 ms, 4 static pages, 16 routes |
| `prek run --all-files` | 0 | all back/front/workspace hooks passed |

**E2E: NOT RUN.** `E2E_TEST_PASSWORD` is not exported and neither disposable
stand endpoint answered (`http://127.0.0.1:16000/api/health` and
`http://127.0.0.1:19000/health` unavailable), so no Playwright grid, clipboard,
collab or 94k-plan scenario was executed. Spec discovery alone was verified:
`bunx --no-install playwright test --project=smoke --list` → 44 tests in 10
files. `clipboard.spec.ts`, `smoke.spec.ts` (single-cell edit + Ctrl+A
disabled), `collab.spec.ts` (undo/redo, presence, leases) and
`workspace-panels.spec.ts` (sorted/filtered facets) remain the specs to run on
a prepared stack. No runtime-performance claim is made for either fix.

## Phase 4.3/4.4 — HTTP DTOs and error contracts (2026-11-27)

### Research (STATIC, from the checked-out branch)

- `docs/API.md` does not exist in ReportCollector (unlike DocMasker); the canonical HTTP contract is `docs/ARCHITECTURE.md`, «API». This section is the frontend-side companion.

- The frontend kept field-by-field HTTP read DTOs in `shared/api/{raw-materials,kppk,logistics,renovation-shared,renovation-*-api,table-export,report-collector}.ts`. The backend declares matching response models (`response_model` on 100+ routes); camelCase field names are intentional on both sides (`back/raw_materials/schemas.py`), so generated types can be used directly.
- Duplicated and drift-prone: raw-materials 11 read DTOs, КППК 9 read rows/views, Логистика 8 read rows/views/enums. These are now aliases to `front/src/shared/api/generated/openapi.d.ts`; exported local names are unchanged.
- Deliberately NOT generated: WS/collab discriminated payloads (`shared/collab/ws-client.ts` — the OpenAPI schema has no `/api/ws/*` paths), UI-normalized `CurrentUser` (Zod transforms `null` → `undefined`), generic envelopes (`KppkApplied<T>`, `LogisticsDictionary<T>`), local grid/session types, and the import/export job DTOs whose backend schemas mark defaulted fields optional while the UI consumes them as required. Generated types do not validate at runtime; boundary validation stays.
- Error handling drift: `requestJson`/`requestWrite`, profile/server reads, table export, renovation export/import and four workspace loaders each parsed errors differently; several `catch`/`.catch(() => null)` blocks turned any failure into a success-shaped empty value and dropped the backend's `X-Request-ID`. The proxy forwarded content headers but not the correlation id.
- Existing RC "freshness" coverage before this change: `docs/RENOVATION-DESIGN.md`'s one-off `/openapi.json` 36/36 renovation-path diff from the router decomposition, and the nightly **dependency** freshness report (`uv tree --outdated`, `bun outdated`, report-only). Neither compared committed frontend declarations with the backend schema.

### Generator decision

- Installed toolchain: TypeScript **7.0.2** (native port) in `front/bun.lock`/`node_modules`, Bun 1.4.4, Node 26. Per the proven DocMasker pattern, `swagger-typescript-api@13.15.0` has **no TypeScript peer** and bundles its own nested TypeScript 6.0.3; no peer override, no second project toolchain, no runtime dependency. `openapi-typescript@7` (TS5 peer, broken on TS7) and `@hey-api/openapi-ts` (TS7 runtime `SyntaxKind` failure) were rejected there for the same reason.
- Commands: `uv run --directory back python -m src.openapi_export <file>` (in-process, no server/DB) → `bun run --cwd front gen:openapi` → `bun run --cwd front gen:openapi --check` (CI `openapi-contract` job). Generator flags: `--no-client --enum-style=union --type-only-imports --disable-formatting --silent`. Output is formatted with the pinned `oxfmt` before comparison, so two runs are byte-identical.

### Type-safe errors

- `shared/api/api-error.ts` is now the single boundary: `readApiDetail` only reads string `detail` / pydantic `msg` (never `input`/`ctx`/`loc`); `readJsonBody` and `parseJsonPayload` reject wrong content type, empty/malformed/non-object bodies instead of casting them into success data; `apiErrorFromResponse` preserves status, Russian detail and `X-Request-ID` (or a typed `request_id`); `fetchFailure` maps network/abort/timeout to fixed Russian messages without echoing browser text.
- `ReportCollectorApiError` lives in `api-error.ts` and is re-exported from `report-collector.ts`. `errorMessage` appends `(ID запроса: …)` so toasts can be correlated with logs.
- The Next proxy forwards `X-Request-ID` and converts upstream network/abort failures into typed JSON errors (502/499) instead of an empty HTML page. Auth config, movement versions, renovation meta/versions and profile reads no longer swallow failures into `null`. Table-export job descriptors are validated field by field; malformed 202 bodies fail.
- Tests cover malformed bodies, wrong content type, network failures, abort/timeout, typed envelopes with request ids, PII-bearing validation fields, job/export/auth-config flows and proxy correlation-id forwarding.

### Validation

Exact command outputs, including the negative freshness proof (tampered
declaration → `gen:openapi --check` exits 1), are recorded in this branch's
commit message; the generator/gate contract itself lives in
`docs/ARCHITECTURE.md`.

## Phase 5.1/5.2/5.3 — motion and accessibility audit (2026-11-27)

Scope: `front/` only. No new dependency, no timing-token consolidation and no
`will-change` was added. **STATIC** means read from source/installed CSS;
**MEASURED** means the exact command/browser output recorded below.

### Inventory (STATIC unless tagged)

| Surface(s) | Property / duration / easing | Frequency | Style / layout / paint / compositor | Essential? | Reduced motion |
|---|---|---|---|---|---|
| `html` | `background-color`, `color` 200 ms ease | theme switch | style + paint | decorative | none |
| `.app-header`, `.app-body`, `.table-toolbar`, `.table-footer` | `max-height`/`min-height`/`padding`/`border-color`/`opacity`/`transform` 260 ms `cubic-bezier(.22,1,.36,1)` | fullscreen toggle | layout + style + paint + compositor | **essential** — toolbar/footer height is what gives the grid the viewport | none |
| `.support-link`, `.expandable-button`, their labels | `gap`/`padding` 180 ms ease; label `max-width` 220 ms, `opacity` 130–140 ms | hover/focus | layout + style | expansion is essential for the label, easing is decorative | none |
| `.toolbar-filters__row`, `.toolbar-collapse`, `.archive-autocomplete` | `max-height`/`max-width`/`margin` 220 ms, `opacity` 140 ms | section switch | layout + style | **essential** — reclaimed toolbar space | `.archive-autocomplete` was a gap; fixed here |
| `.form-status__selection` | `grid-template-rows: 0fr → 1fr`, `opacity` 260 ms | selection change | layout + style | decorative height smoothing | none |
| `.form-status__dot--connecting`, `--import` | `collab-pulse` opacity 1.2 s ease-in-out **infinite** | continuous while connecting/import | compositor | essential status signal | none |
| `.action-bar > *` | `action-button-enter` opacity + `translateX(.4rem)` 180 ms `cubic-bezier(.22,1,.36,1)` | conditional action mount | compositor | decorative | none |
| `.fullscreen-panel` | opacity 180 ms, transform 240 ms, `visibility 0s linear 240ms` | hover reveal | compositor + visibility | decorative | none |
| `.grid-header-menu__popover` | `grid-header-menu-in/out` opacity + translate + scale 160/120 ms | header context menu | compositor | decorative | none |
| `.column-filter__popover`, `.row-go-to-filter__popover` | `plan-filter-enter/exit` opacity + translate + scale 180/140 ms | filter/go-to open | compositor | decorative | none |
| `.data-grid revogr-header .header-content`, `.sort-indicator`, `.report-header__title`, `.header-filter-button`, funnel labels | `margin`/`width`/`padding` 267 ms `cubic-bezier(.4,0,.2,1)`, opacity 267 ms | sort/filter/hover | layout + style; funnel button geometry is the hit area | mixed: label shift decorative, funnel expansion essential | none; two label gaps fixed here |
| `.renovation-grid__header-slot`, `__header-cell` | `margin-top` 240 ms ease; cell opacity 180 ms | header pin toggle | layout + compositor | **essential** — the grid must grow when the header is unpinned | none |
| `AnimatedPanel` (WAAPI) | height 260 ms `cubic-bezier(.22,1,.36,1)` | toolbar/footer content resize | layout | essential (panel follows its tallest control) | guarded by `matchMedia("(prefers-reduced-motion: reduce)")` |
| HeroUI 3.2.8 overlays (installed CSS) | popover/dropdown/tooltip `heroui-overlay-enter-90` 150 ms / exit 100 ms; select enter-95 150 ms; autocomplete enter-95 250 ms; modal 150–250 ms; opacity + `translate3d` + scale | overlay open/close | compositor | decorative | modal/alert-dialog/toast already use `motion-reduce:animate-none`; **popover/dropdown/tooltip/select/autocomplete did not** (fixed here) |
| HeroUI triggers/fields | color/background/box-shadow/transform 150–250 ms | hover/focus/state | style + compositor | decorative | their own `motion-reduce:transition-none` stays untouched |

No app-owned transition uses `all`, no permanent/global `will-change`, and no
new timing token was introduced: the ~15 ad-hoc durations stay deferred as
recorded in `docs/TODO.md`, because no visual-regression comparison was made.

### Measured jank and the decision not to swap layout animations

CDP `Performance.getMetrics` around four «Закрепить заголовок» toggles on the
seeded «Ремонт» plan (1440×900 Chromium, 320 ms sampling window per toggle,
`front/e2e`-independent temp harness; **MEASURED**):

| Motion | LayoutCount / toggle | RecalcStyleCount / toggle | LayoutDuration | RecalcStyleDuration |
|---|---:|---:|---:|---:|
| normal 240 ms `margin-top` transition | 26–31 | 45–46 | 0.0109–0.0174 s | 0.0735–0.0915 s |
| reduced motion (instant layout) | 5–28 | 20–37 | 0.0017–0.0065 s | 0.0356–0.0543 s |

The extra per-frame layout is real, but it is the **essential** grid resize:
the plan's data viewport must grow while the 11 header levels leave. Replacing
it with a transform would either leave the grid short by the header height or
make the header overlap live rows during the slide, and would change RevoGrid
re-measurement/pointer geometry (a documented landmine). The layout cost was
therefore **kept**, not papered over; the decorative fades stay opacity-only.
No before/after CSS swap was claimed because none was made.

### Demonstrated defects fixed

1. Under `prefers-reduced-motion: reduce`, computed styles on the old build
   (**MEASURED** at `http://127.0.0.1:16000`) still showed
   `.archive-autocomplete` at `0.22s/0.14s` and both
   `.renovation-grid__header-label` and `.renovation-grid__number-label` at
   `0.267s`; they are now in `styles/11-reduced-motion.css` and re-measure as
   `transition-property: none`, `transition-duration: 0s`.
2. `animationstart` on the old build under reduced motion (**MEASURED**)
   captured `heroui-overlay-enter-90-ny`, `heroui-overlay-exit` and
   `heroui-overlay-enter-95-ny`. HeroUI's CSS has no `motion-reduce:animate-none`
   for these five overlay families. A targeted app-side override in the same
   final partial makes their enter/exit instant; the same harness now reports
   `[]` under reduced motion and the unchanged three events at normal motion.
   React Aria's `useExitAnimation` ends an exit when no animation is running, so
   portals unmount immediately and no focus is left inside them. HeroUI's own
   reduced-motion opt-outs (modal, alert dialog, toast, fields and triggers) are
   untouched — this is not a blanket `animation: none`.
3. `HoverDropdown` has no polling loop: the 60 ms interval is absent, pointer
   coordinates come from one passive `pointermove` listener, geometry is
   coalesced into one RAF and outside dismissal uses one cancellable 250 ms
   timeout. A generation guard invalidates frames/timers from a previous open
   and all three effects clean up on unmount (**STATIC** reading; unit test
   `hover-dropdown.test.tsx` pins "no interval" and listener removal).

### Keyboard / a11y checks (MEASURED, Chromium)

- Keyboard opening of «Вид таблицы» sets `aria-expanded="true"` and a visible
  `data-focus-visible="true"` trigger; focus moves into the menu and returns to
  the trigger on Escape with no focus left in the removed portal.
- Action-bar buttons all expose `aria-label`; disabled ones use native
  `disabled`, enabled ones keep `tabindex="0"`, none sets a positive tabindex.
- These are targeted browser assertions, **not** a full accessibility audit: no
  automated axe scan was added and no screen-reader/manual WCAG review was run.
  A clean axe run would still not prove keyboard/screen-reader usability; the
  tests above only cover the named interaction paths.

### Validation (MEASURED)

`bun run --cwd front test:e2e --project=smoke ...` was run against a local
production build served on the stand origin `http://127.0.0.1:16000` with
`E2E_TEST_PASSWORD` (the stand front container was temporarily replaced by the
branch build; its backend/Postgres/Valkey were untouched). Exact results:

| Command | Result |
|---|---|
| `bun run --cwd front format:check` | pass, 312 files |
| `bun run --cwd front lint:check` | 0 errors, 688 pre-existing oxlint warnings, stylelint clean |
| `bun run --cwd front typecheck` | no type errors, 293 files |
| `bun run --cwd front test` | 763 pass / 0 fail / 2,211 `expect()` calls / 98 files |
| `bun run --cwd front build` | compiled 668 ms, TypeScript 486 ms, 4 static pages, 16 routes |
| `prek run --all-files` | all back/front/workspace hooks passed |
| targeted `smoke.spec.ts hover-dropdown.spec.ts` | **15 passed / 1 failed** — the failure is the pre-existing price-edit/undo case below |
| full `--project=smoke` | **46 passed / 1 failed / 47 total** (discovery: 47 tests in 10 files; baseline was 44) |
| pristine branch head (changes stashed), same price-edit test | **1 failed** — the same failure reproduces without any change from this task |

The failing case, `production forms load and a price edit survives reload`,
sends a successful `PATCH /api/raw-materials/prices` but the «Отменить действие»
button stays disabled (no client undo increment). It reproduces on the
unmodified branch head, is unrelated to motion/a11y and is deliberately not
changed here. Every new/extended motion and keyboard assertion passes.

## Remaining work

The stateful half of 5.2–5.3 — splitting the workspace's data loading,
collaboration, commit/undo and dialog composition, and the grid's imperative
selection/clipboard/scroll integration — is still pending. The pure selector,
geometry, header-chain and value-normalization boundaries are already extracted
and tested (see above); `renovation-grid.tsx` deliberately keeps its RevoGrid
contracts, memo identities and JSX structure intact. The measured before/after
runtime scenarios are also pending: they require React DevTools/Performance
recordings and the prepared stack; no performance claims are made here. Stage 9
(CSS split) is complete — see above. §8.4 (small shared UI duplication) was
compared across the two independent deployments: support-button contracts,
brand switchers and domain-specific theme tokens differ by product requirement,
so no forced convergence was applied.
