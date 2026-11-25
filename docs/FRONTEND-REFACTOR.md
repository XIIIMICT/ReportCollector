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

## Zustand decision

**Not needed.** Panel filters remain URL-backed per tab
(`use-toolbar-filters.ts`), grid filters/drafts/sorting remain scoped by
project and section in localStorage (`use-grid-column-filters.ts`), transient
state stays local, and RevoGrid owns its own interaction state. No measured
rerender or cross-branch state problem has been demonstrated yet; adding a
store would multiply canonical sources without fixing one.

## Remaining work

Stages 5.2–5.3 (RenovationWorkspace/Grid decomposition), 8 (breakpoints,
reduced motion, HoverDropdown), 9 (CSS split) and the measured before/after
runtime scenarios are still pending. They require the prepared stack and
React DevTools/Performance recordings; no claims about them are made here.
