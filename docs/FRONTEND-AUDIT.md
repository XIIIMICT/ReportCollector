# Frontend audit & remediation — ReportCollector (2026-11-27)

## 1. Executive summary

Audited commit `f53ea9cf78bc6a1c8cd56b5123fec45bd8039de1`; final local `dev` =
`8e5fdb7`. Environment: Bun 1.4.4, Node v26.13.1, Next 16.4.6, React/ReactDOM 19.3.1,
HeroUI 3.2.8, Tailwind 4.3.5, TypeScript 7.0.2, `@revolist/react-datagrid` /
`revogrid` 4.32.0, `rv-pro-trial` 3.0.18, Zod 4.6.7, Playwright 1.65.1. Task branches
merged `--no-ff`; nothing pushed.

Four verified defects were fixed: a dead Webpack-only bundle analyzer, two asymptotically
unsuitable grid hot paths, duplicated handwritten HTTP DTOs / unsafe error handling, and
incomplete reduced-motion coverage. No runtime dependency was added; one dead dev
dependency was removed. All canonical checks pass; 17/17 targeted grid/collab E2E passed
on the available stand.

## 2. Baseline and after measurements

Baseline: `docs/FRONTEND-AUDIT-BASELINE.md` (`deb3465`), captured with `next analyze`
(Turbopack analyzer in installed Next 16.4.6). Historical ~3,722.9 KB raw / 1,198.2 KB
gzip across 37 chunks was confirmed to match the current build within noise; it is not a
target.

| Measure (MEASURED) | Baseline | After |
|---|---:|---:|
| `/` JS | 646,795 B raw / 198,859 B gzip | unchanged |
| `/[project]` JS | 795,529 B raw / 247,329 B gzip | unchanged |
| All 37 JS chunks | 3,813,115 B raw / 1,215,001 B gzip | unchanged |
| Largest chunk | 1,533,708 B raw / 509,377 B gzip, **async** | unchanged |
| Route CSS | 545,123 B raw / 56,152 B gzip | unchanged except motion rules |
| Unit tests | 735 pass | **763 pass** |
| Build | 16 routes, 4 static pages | 16 routes, 4 static pages |
| E2E discovery | 44 tests / 10 specs | 47 tests / 10 specs |
| Typed HTTP DTO source | hand-copy | generated `openapi.d.ts`, CI freshness |

No bundle improvement is claimed: the two largest chunks are async and absent from every
route's initial JS list, so splitting them would not improve initial navigation. The
verified wins are algorithmic, contract and accessibility correctness.

## 3. Verified findings (severity / impact)

| # | Severity | Finding & evidence | Fix |
|---|---|---|---|
| 1 | P2, tooling | `@next/bundle-analyzer@16.4.6` bails on `process.env.TURBOPACK`; `bun run analyze` printed "no report will be generated", no output produced. Installed Next 16.4.6 supports `next analyze` (alias `experimental-analyze`). | Removed dead wrapper/dependency via `bun install`, added `analyze` script, documented the correct workflow (`8d65f24`). |
| 2 | P1, large-workspace scalability (STATIC) | `data-grid.tsx` row-header copy did `rows.find` per selected id: **O(S·N)**; `renovation-workspace.tsx` rescanned every row × ~217 fields per `cell:changed` frame: **O(W·F)**. | `rowsForSelectedIds` keyed map → **O(N+S)**; `remoteCellPatchesByRow` inverse index → **O(R+W)**, preserving line order, duplicates, patch order, pending replay and derivation (`0cac7fd`). |
| 3 | P1, contract/maintainability | HTTP DTOs hand-copied across `raw-materials`, `kppk`, `logistics`, `renovation-*`, `table-export`; error paths turned failures into success-shaped empty data and could leak Pydantic `input`/`ctx`/`loc`. | Generated compile-time DTOs from `app.openapi()` with pinned dev-only `swagger-typescript-api@13.15.0` (`--no-client`), CI freshness gate; typed errors keep `detail`, status and `X-Request-ID`, fixed Russian messages (`6725167`). |
| 4 | P2, accessibility | Under `prefers-reduced-motion`, three app-owned selectors still had 0.22–0.267 s transitions and HeroUI overlay enter/exit keyframes still fired (`animationstart` captured). | Targeted `animation:none` for HeroUI popover/dropdown/tooltip/select/autocomplete keyframes plus app selectors; HeroUI modal/toast/field rules untouched (`8e5fdb7`). |

Not changed after verification: `HoverDropdown` has no polling interval (pointermove → one
RAF → one cancellable 250 ms timeout with generation guard and full cleanup); the 683
oxlint warnings are mostly React-Compiler-deferred `react-perf` suggestions, not failures;
the largest chunks are async, so no eager-split was justified.

## 4. Implemented changes and design rationale

- **Analyzer**: one documented command (`bun run analyze`) producing a snapshot plus
  `graph.jsonl`; generated output gitignored. `bun install --frozen-lockfile` re-verified.
- **Grid**: smallest correct indexing at the existing lifecycle boundary; RevoGrid callback
  identity, event order, refs, imperative handles, selection restoration, undo/rollback,
  paste ordering and collaboration seq/delta/lease semantics were preserved. No generic
  grid framework and no unification of `data-grid.tsx` with `renovation-grid.tsx`.
- **API/errors**: deterministic in-process `back/src/openapi_export.py`; generated types
  are compile-time only, not runtime validation. WS/collab discriminated unions and
  UI-only types remain handwritten. Proxies forward `X-Request-ID` and return typed
  502/499 JSON; no PII/telemetry echoes.
- **Motion**: only reduced-motion rules changed; no global `will-change`, no timing-token
  consolidation, no animation library. Grid resize cost (26–31 layouts per header-pin
  toggle) is essential, so no decorative transform swap was made.

## 5. Rejected alternatives (Phase 7 decision record)

| Candidate | Decision | Evidence / current solution |
|---|---|---|
| Splitting the ~1.5 MiB async chunk | Reject | It is absent from all initial route JS lists; no measured initial-load gain. |
| `@next/bundle-analyzer` | Remove | Dead under Turbopack; `next analyze` is the installed-version analyzer. |
| Web Worker for grid transforms | Reject | No measured main-thread blocking (94k fixture BLOCKED); transfer/copy cost unproven. |
| Zustand / Redux Toolkit / RxJS / TanStack Query / Framer Motion / RHF / virtualization / microfrontends | Reject | No measured cross-tree state, caching, stream or animation problem the current React/HeroUI/RevoGrid model cannot solve. |
| Blanket memoization | Reject | React Compiler is enabled; explicit memo only where a semantic/perf benefit is demonstrated. |
| Timing-token consolidation | Defer | No visual regression harness; prior refactor deliberately deferred it. |
| Regenerate WS protocol from a schema | Defer | No WS paths in `app.openapi()`; a typed discriminated union is maintained and contract-tested. |

New dependency in this audit: **none at runtime**. Dev-only `swagger-typescript-api@13.15.0`
has no TypeScript peer and brings its own nested TS 6.0.3 — no unsafe override or second
toolchain; it matches the pattern already proven in DocMasker.

## 6. Validation (exact commands and outcomes)

- `bun run --cwd front format:check` — 312 files, pass.
- `bun run --cwd front lint:check` — 0 errors (688 pre-existing warnings), stylelint clean.
- `bun run --cwd front typecheck` — no errors in 293 files.
- `bun run --cwd front test` — **763 pass / 0 fail**, 2,212 `expect()` calls, 98 files.
- `bun run --cwd front build` — compiled 668 ms, TS 486 ms, 4 static pages, 16 routes.
- `bun run --cwd front gen:openapi --check` — fresh; tampering exited 1, regeneration restored.
- Backend `ruff check`, `ruff format --check`, `ty check`, `test_openapi_export.py` — pass.
- `prek run --all-files` — all hooks passed at every merge.
- Actionlint via pinned `rhysd/actionlint:1.7.7` — exit 0.
- `bunx --no-install playwright test --project=smoke --list` — 47 tests / 10 specs.
- Targeted grid/collab E2E on `E2E_BASE_URL=http://127.0.0.1:16000` with the reserved
  `.test` identity: `clipboard.spec.ts`, `collab.spec.ts`, `workspace-panels.spec.ts` —
  **17 passed / 0 failed** in 57.1 s.
- Full smoke on a branch build: 46 passed / 1 failed. `production forms load and a price
  edit survives reload` fails **identically on the pristine branch head** (verified by
  stashing changes and rebuilding) — pre-existing, unrelated, not modified.

## 7. Phase 6/8 notes

Deduplication: generated DTOs replaced hand-copied interfaces; two repeated grid scans
became small tested helpers. No two-line wrappers, generic hooks, `utils.ts` dumping
ground or cross-repository package. Tests added for pure algorithms (copy/paste index,
remote patch inversion), API error parsing (malformed bodies, wrong content type, abort,
correlation id), and reduced-motion/keyboard/focus behavior. Analyzer snapshots and traces
are local artifacts, not committed.

## 8. Remaining work / limitations

- The ~94k-row plan fixture is absent (disposable DB has 4 plan rows / 67 links / 134
  movement rows): large-grid browser profiling, FPS/long-task and React Profiler evidence
  are BLOCKED, so no runtime speedup is claimed.
- One pre-existing smoke failure (`price edit survives reload`) reproduced on pristine
  head; needs a separate task with a prepared stand.
- 300/500-client presence, production-image stacks, target-hardware profiling, axe scan,
  manual WCAG review and visual regression for timing tokens were not performed; keyboard
  and focus assertions are not proof of full accessibility.
- 688 oxlint warnings remain (React Compiler-deferred); changing lint policy is out of scope.

### Conditional phases — explicit status

- **1.2 Server/Client boundaries:** reviewed. Static layout/metadata/config stay in Server
  Components; the grid/collaboration islands are interactive by definition. No server
  module was found in a client graph and no boundary move was justified by evidence — no
  change.
- **2.3 Stateful decomposition of `renovation-grid.tsx` / `renovation-workspace.tsx`:**
  DEFERRED. The two verified hot paths were fixed with pure, tested helpers; extracting
  the remaining stateful controllers would move RevoGrid callback/ref/ownership contracts
  with no measured payoff, and the full integration suite for those contracts is itself a
  documented deferral. Revisit only with a prepared large-workspace fixture.
- **4.2 TypeScript expressiveness:** no unsafe `any`/blanket assertions were found in the
  audited hot paths; the two generated-DTO and error-contract changes use the existing
  discriminated unions and `satisfies`-style exact typing. No type gymnastics added.

## 9. Post-audit fixes (maintainer feedback / CI)

- Header support control was invisible on the isolated CI stand because
  `infra/compose.ci.yaml` had no `SUPPORT_MAIL`; added a clearly synthetic fixture and an
  E2E visibility guard (`d6fa6a2`).
- `gen:openapi --check` failed in CI: importing `app` constructs `Settings`, which
  required PostgreSQL/AUTH env. The export entrypoint now injects synthetic placeholders
  before import (never overrides real values, no connection) with a clean-cwd test
  (`33671c9`).
- Full smoke was red: the hardened JSON parser rejected the legitimate scalar count
  returned by price/movement PATCHes, so `undoCount` never incremented, and a slow
  mount-time `/undo-state` read could roll back a newer commit. Added opt-in scalar
  parsing for those two callers and a monotonic undo epoch; full smoke 48/48
  (`c39df43`). No test was skipped or loosened.

## 10. Cross-project comparison (ReportCollector vs DocMasker)

- **Intentionally different:** RC is a RevoGrid + WebSocket collaboration app with URL-
  backed filters and per-grid persisted view settings; DM is a client-side DOCX masking
  workflow with generation-private staging hosts and preview-only marks. Do not unify
  their state models or grid/DOCX processing.
- **Already consistent:** Next 16.4.6 / React 19.3.1 / HeroUI 3.2.8 / TS 7.0.2; React
  Compiler with no blanket memoization; Turbopack `next analyze`; generated OpenAPI DTOs
  that are compile-time only; no runtime SDK; fail-closed validation of untrusted data.
- **Meaningful inconsistency worth a separate task:** RC's 688 pre-existing oxlint
  warnings vs DM's clean lint; RC's one pre-existing smoke failure; different coverage
  gates (RC 80% combined backend / DM 80% backend + 65% frontend are intentional).
- **Dependencies:** no runtime library was added in either repo; RC removed a dead
  Webpack-only analyzer; both keep `swagger-typescript-api` as a pinned dev-only generator
  with a CI freshness check.
- **Deferred jointly:** large-fixture browser profiling, target-hardware memory drills,
  axe/manual WCAG validation, visual regression for timing tokens.
