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

## Zustand decision

**Not needed.** Panel filters remain URL-backed per tab
(`use-toolbar-filters.ts`), grid filters/drafts/sorting remain scoped by
project and section in localStorage (`use-grid-column-filters.ts`), transient
state stays local, and RevoGrid owns its own interaction state. No measured
rerender or cross-branch state problem has been demonstrated yet; adding a
store would multiply canonical sources without fixing one.

## Remaining work

The remaining (higher-risk) part of 5.2–5.3 — splitting the workspace's data
loading, collaboration, commit/undo and dialog composition, and the grid's
column/header/selection logic — is still pending. Stages 8 (breakpoints,
reduced motion, HoverDropdown), 9 (CSS split) and the measured before/after
runtime scenarios are also pending. They require React DevTools/Performance
recordings and the prepared stack; no claims about them are made here.
