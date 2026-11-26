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
202.4 KB, 163.7 KB, 132.0 KB. The analyzer setup is `bun run analyze`
(`@next/bundle-analyzer`); no analyzer report was generated for the baseline
build because the default build output already gives comparable totals.

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

## End-to-end smoke (CI stack, 127.0.0.1:16000)

`E2E_BASE_URL=http://127.0.0.1:16000 KEYCLOAK_ENABLED=false bun run test:e2e
--project=smoke --reporter=list`:

| Run | Result |
|---|---|
| Baseline (before the extractions) | 30 passed, 1 failed, 3 skipped |
| Repeat run on the same CI stack | 30 passed, 1 failed, 3 skipped |

The CI stack serves a **prebuilt production image**, so these two runs are an
environment baseline, not a verification of the refactor; the refactor is
verified separately against the dev stack (`:5000`, compose `--watch`).
The single failure is identical in both runs and occurs before any refactoring:
`raw-materials-ui.spec.ts:59` times out in `loginAsLocalUser` (`waitForURL`,
30 s) on the CI stack. It is recorded as a pre-existing/environment issue, not
a regression; no e2e assertion was weakened to hide it. No DOM/row-count
performance numbers were captured yet — that needs a DevTools/Performance
recording on a prepared stack.

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
