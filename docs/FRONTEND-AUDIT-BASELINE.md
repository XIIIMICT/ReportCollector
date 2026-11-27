# Frontend audit — Phase 0 reproducible baseline (ReportCollector)

Scope: read-only research before optimization. No code, config, dependency,
remote or Git changes were made. The only new file is this report (untracked,
not committed). Commands were run from `front/` unless stated otherwise.
Numbers are tagged **MEASURED** (command output / installed file), **STATIC**
(source/config reading only), **HISTORICAL** (previous recorded build, not a
target) or **BLOCKED** (not runnable here). Gzip values in this report were
computed with .NET `GZipStream(CompressionLevel.Optimal)` over the exact emitted
files; a different compressor/settings will give slightly different numbers.

## 1. Repository and toolchain snapshot

| Item | Value | Tag |
|---|---|---|
| Repository | `D:\Work\ReportCollector` | MEASURED |
| Branch | `dev` (tracking `origin/dev`) | MEASURED |
| HEAD | `f53ea9cf78bc6a1c8cd56b5123fec45bd8039de1` (2026-11-27T20:12:32+03:00, `merge(fix): collab task cleanup into dev`) | MEASURED |
| Worktree | single worktree `D:/Work/ReportCollector`; `git status --short --branch` = `## dev...origin/dev` (clean at start) | MEASURED |
| Bun / Node actually used | `bun 1.4.4`, `node v26.13.1` | MEASURED |
| Platform | win32, PowerShell; CI pins Bun 1.4.4 and Node 26 | MEASURED / STATIC (`.github/workflows/ci.yaml:183,186,265,268`) |
| Lockfile | `front/bun.lock`, `lockfileVersion: 2`; `bunfig.toml` sets `install.exact = true` and scope `@revolist` → `https://trial.rv-grid.com` | MEASURED / STATIC |
| Install command | `bun install --frozen-lockfile` (CI: `ci.yaml:187,331`) | STATIC |
| Build mode | Next.js **Turbopack** is the default for `next build` in this version; `next.config.ts` has no webpack override, and the build banner prints `(Turbopack)` | MEASURED / STATIC |

Exact installed versions (read from `node_modules/<pkg>/package.json` and
cross-checked against `bun.lock`):

| Package | Installed | Tag |
|---|---|---|
| next | 16.4.6 | MEASURED |
| react / react-dom | 19.3.1 / 19.3.1 | MEASURED |
| @heroui/react / @heroui/styles | 3.2.8 / 3.2.8 | MEASURED |
| tailwindcss / @tailwindcss/postcss | 4.3.5 / 4.3.5 | MEASURED |
| typescript | 7.0.2 (native port) | MEASURED |
| @revolist/react-datagrid | 4.32.0 (trial tarball) | MEASURED |
| @revolist/revogrid (override) | 4.32.0 (trial tarball) | MEASURED |
| @revolist/rv-pro-trial | 3.0.18 | MEASURED |
| @next/bundle-analyzer | 16.4.6 (webpack-only) | MEASURED |
| @playwright/test | 1.65.1 | MEASURED |
| zod / jose / undici / iconoir-react / next-themes | 4.6.7 / 6.2.18 / 8.13.1 / 7.12.2 / 0.4.6 | MEASURED |
| @internationalized/date / react-aria / react-aria-components / react-stately | 3.12.4 / 3.53.1 / 1.22.1 / 3.51.0 | MEASURED |
| oxlint / oxfmt / stylelint / happy-dom / @testing-library/react / babel-plugin-react-compiler | 1.95.0 / 0.80.0 / 18.1.0 / 21.0.4 / 16.3.3 / 1.0.0 | MEASURED |

Scripts in `front/package.json` (**STATIC**): `dev`, `build`, `start`,
`analyze` (`ANALYZE=true next build`), `lint` (`oxlint --fix && stylelint
--fix`), `lint:check` (`oxlint && stylelint`), `format` (`oxfmt`),
`format:check` (`oxfmt --check`), `test` (`bun test`), `test:coverage` (`bun
test --coverage --coverage-reporter=lcov && bun scripts/coverage-gate.ts`),
`test:e2e` (`playwright test`), `typecheck` (`bun check`). There is no dedicated
typecheck-with-tsc script; CI additionally runs `bunx --no-install next
typegen` before `bun run typecheck`.

Requested `docs/TESTING.md` does **not** exist in this repository (**MEASURED**;
the sibling `DocMasker` repo has one). Testing rules live in root/front
`AGENTS.md`, `docs/WORKFLOW.md` and `docs/TODO.md`.

## 2. Canonical checks (all from `front/`)

| Command | Exit | Result | Tag |
|---|---:|---|---|
| `bun run format:check` | 0 | `oxfmt --check`: all matched files correct, **307 files**, 321 ms | MEASURED |
| `bun run lint:check` | 0 | `oxlint` + `stylelint`: **0 errors, 683 warnings** (all oxlint; stylelint printed nothing). Concentrated in `src/features/renovation/renovation-grid.tsx`, `src/shared/grid/data-grid.tsx`, `src/features/renovation/renovation-workspace.tsx`: `react-perf/jsx-no-new-*-as-prop`, `no-shadow`, `no-underscore-dangle` (`__pinnedCopy`, `__pinNumber`, …), `react-hooks/exhaustive-deps`-style deps warnings. Pre-existing by policy: `oxlint.config.ts:27-36` keeps `perf` at `warn` because React Compiler memoizes props. | MEASURED |
| `bun run typecheck` | 0 | `bun check`: no type errors in **290 files**, 478 ms. Run without the CI-only `next typegen` pre-step. | MEASURED |
| `bun run test` | 0 | `bun test` v1.4.4: **735 pass / 0 fail**, 2157–2158 `expect()` calls, **96 files**, 8.32–9.47 s across two runs. Non-fatal noise: `DBGOPT …` console lines from `src/shared/grid/__tests__/grid-editors.test.tsx`. | MEASURED |
| `bun run build` | 0 | Next 16.4.6 (Turbopack), `.env.local` loaded. Compiled **1062 ms**, TypeScript **1172 ms**, 4/4 static pages in **131 ms**, **16 App Router routes** + middleware proxy. No build warnings. Warm rebuild: 484 ms compile / 765 ms TS / 146 ms static. | MEASURED |

Route list (**MEASURED**, 16 rows): `/`, `/_not-found`, `/[project]`,
`/blocked` + 12 `/api/…` routes (`auth/callback`, `auth/config`,
`auth/logged-out`, `auth/login`, `auth/logout`, `collab/ticket`, `health`,
`kppk/[...path]`, `logistics/[...path]`, `me`, `raw-materials/[...path]`,
`renovation/[...path]`). `ƒ` = dynamic, `○` = static.

**E2E: NOT RUN (BLOCKED in this phase).** No browser suite, profiling, FPS,
long-task or React Profiler recording was taken — the brief forbids heavy
browser profiling in Phase 0 and the shell does not export `E2E_TEST_PASSWORD`.
Readiness was checked: `bunx --no-install playwright test --project=smoke --list`
exits 0 and discovers **44 tests in 10 spec files** (MEASURED); the isolated
stand is up (see §4). Do not interpret any of the above as an E2E pass.

## 3. Performance baseline

### 3.1 Turbopack-aware analyzer supported by the installed Next.js

**MEASURED** from `node_modules/next/dist/bin/next` (lines 105, 143, 146, 158,
188-193, 304) and the shipped docs
`node_modules/next/dist/docs/01-app/03-api-reference/06-cli/next.md:52,102,227-288,435-440`:

- the canonical command is **`next analyze`**; `next experimental-analyze` is a
  hidden **alias** (both work in 16.4.6);
- `next build --analyze` generates analysis data during a Turbopack build;
- **`next analyze --output --snapshot <name>`** writes `<distDir>/diagnostics/analyze`
  without starting the UI server;
- **`next analyze export [--snapshot …] [--route …]`** streams a saved graph as
  JSON Lines (added in v16.4.0);
- `next internal static-routes-info --json --files` reports per-route client
  JS/CSS/maps and server categories.

Commands actually run (all exit 0):

```powershell
bunx --no-install next internal static-routes-info --json --files
bunx --no-install next analyze --output --snapshot baseline-f53ea9c
bunx --no-install next analyze export --snapshot baseline-f53ea9c
```

Artifacts (local temp, **not** committed):
`C:\Users\Scorpi-ON\AppData\Local\Temp\opencode\frontend-baseline-2026-11-27\`
— `static-routes-info.json` (451 KB), `analyze-graph.jsonl` (9.8 MB, schema v1,
1 meta / 3123 module / 17 route / 2144 output / 10430 part / 109 group
records), `analyze-ui\`, `analyze-capture.txt`, `build.txt`, `build-final.txt`,
`unit-test.txt`, `legacy-analyze.txt`, `smoke-discovery.txt`.

### 3.2 Route-specific client assets (MEASURED)

Computed by summing the exact files listed by `static-routes-info --json --files`
in the canonical build, then gzipping those files. KiB = bytes/1024.

| Route | JS files | JS raw (KiB) | JS gzip (KiB) | CSS raw (KiB) | CSS gzip (KiB) |
|---|---:|---:|---:|---:|---:|
| `/` | 11 | 646,795 (631.8) | 198,859 (194.2) | 545,123 (532.3) | 56,152 (54.8) |
| `/_not-found` | 11 | 646,795 (631.8) | 198,859 (194.2) | 545,123 (532.3) | 56,152 (54.8) |
| `/[project]` (workspace) | 13 | **795,529 (776.9)** | **247,329 (241.5)** | 545,123 (532.3) | 56,152 (54.8) |
| `/blocked` | 12 | 651,191 (636.0) | 200,241 (195.5) | 545,123 (532.3) | 56,152 (54.8) |
| 12 `/api/*` routes | 0 | 0 | 0 | 0 | 0 |

Emitted CSS files (**MEASURED**): `3irpl8x7bktzk.css` 545,086 B / 56,095 B gzip
(app styles) + `2x883cuvixom8.css` 37 B / 57 B gzip = the route CSS above. A
third CSS chunk, `12rjs38kcanz9.css` **363,722 B / 53,599 B gzip**, is the
`@revolist/rv-pro-trial` stylesheet (analyzer identifies
`node_modules/@revolist/rv-pro-trial/dist/rv-pro-trial.css` 363,766 B pre-minify
in the `/[project]` client graph); it is loaded with grid sections rather than
counted in the base route CSS list.

### 3.3 All emitted client JS chunks (MEASURED, canonical `bun run build`)

37 `*.js` chunks under `.next/static/chunks`: **3,813,115 B raw / 1,215,001 B
gzip = 3,723.7 KiB / 1,186.5 KiB**. Largest chunks:

| Chunk | Raw (KiB) | Gzip (KiB) |
|---|---:|---:|
| `2ovz35xvilt3m.js` | 1,533,708 (1,497.8) | 509,377 (497.4) |
| `2u0ay8f8daxzb.js` | 671,034 (655.3) | 201,197 (196.5) |
| `3r-okv0vpv6lc.js` | 207,263 (202.4) | 65,426 (63.9) |
| `1rlh12fk-j_bw.js` | 167,627 (163.7) | 45,704 (44.6) |
| `1rsbc-9dq32dj.js` | 134,678 (131.5) | 41,672 (40.7) |
| `08qwhdk900usz.js` | 129,888 (126.8) | 41,689 (40.7) |

The two largest emitted chunks are **async**: neither appears in any route's
`clientJs.files` list (only 14 distinct JS files are route-level). They are
loaded when a project workspace is opened, not on initial `/[project]` HTML.

**HISTORICAL ONLY** (`docs/FRONTEND-REFACTOR.md:22-26`): ~3,722.9 KB raw /
1,198.2 KB gzip / 37 chunks, largest ~1,497.8 KB then ~653.8 KB. Current raw
total and largest chunk match it within noise; the gzip difference (~11.7 KiB)
is not evidence of a regression because the compressor/settings are not the
same. This figure is a reference, **not a target**.

### 3.4 Largest modules and import chains (analyzer, MEASURED)

The analyzer's `part.size` values are **module source bytes before
minification**, not emitted chunk bytes; use them for composition, not for
transfer-size claims. Top client modules on `/[project]`:

| Module (analyzer `source_path`) | Pre-minify raw | Pre-minify gzip |
|---|---:|---:|
| `node_modules/@revolist/rv-pro-trial/dist/rv-pro-trial.js` | 3,065,576 | 1,017,012 |
| `src/app/globals.css` | 545,130 | 55,811 |
| `node_modules/@revolist/rv-pro-trial/dist/rv-pro-trial.css` | 363,766 | 53,165 |
| `node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.production.js` | 203,510 | 63,931 |
| `src/shared/grid/data-grid.tsx` | 169,444 | 56,796 |
| `node_modules/@revolist/revogrid/dist/esm/index-hTHfPPLB.js` | 137,572 | 48,060 |
| `node_modules/zod/v4/core/schemas.js` | 130,840 | 35,940 |
| `src/shared/ui/column-filter-popover.tsx` | 49,348 | 18,004 |
| `src/features/renovation/renovation-grid.tsx` | 41,660 | 13,704 |

Full import chain from the `/[project]` route entry to the dominant module
(reverse-BFS over `module` dependency records):

```
app-page.js?page=/[project]/page
  → src/app/[project]/page.tsx (RSC)
  → src/features/report-workspace/report-workspace.tsx (RSC → client reference)
  → src/features/report-workspace/report-workspace.tsx [app-client]
  → src/features/kppk/kppk-workspace.tsx [app-client] (next/dynamic entry)
  → src/features/kppk/kppk-workspace.tsx [app-client]
  → node_modules/@revolist/rv-pro-trial/dist/rv-pro-trial.js
```

Direct importers of `rv-pro-trial.js` (MEASURED from the graph): `kppk-workspace.tsx`,
`kppk-realization-section.tsx`, `renovation-grid.tsx`, `logistics-workspace.tsx`
(**STATIC** source confirmation: `CellMergePlugin`/`InfinityScrollPlugin`
imports plus `rv-pro-trial/styles.css` in `renovation-grid.tsx:17,32`). This is
the highest-value target for any later split work; no change was made here.

### 3.5 Verdict on `@next/bundle-analyzer` (MEASURED)

The configured plugin is **dead/misleading for the actual Turbopack build**:

1. `next.config.ts:1-6,62` wraps the config in `bundleAnalyzer({ enabled:
   ANALYZE==="true" })`, and `package.json` exposes `analyze: "ANALYZE=true next build"`.
2. Installed `node_modules/@next/bundle-analyzer/index.js:7-15` checks
   `process.env.TURBOPACK`, prints *"The Next Bundle Analyzer is not compatible
   with Turbopack builds, no report will be generated … To run this analysis
   pass the `--webpack` flag to `next build`"*, and returns the config unchanged.
   It only registers a `webpack(config)` hook (`:20-39`), which a Turbopack
   build never calls.
3. `bun run analyze` (exit 0) printed exactly that warning. No `analyze/`,
   `.next/analyze/`, `client.html` or `client.json` was created; the only
   `analyze` directory under `front/` is `.next/diagnostics/analyze` from the
   separate `next analyze --output` run (timestamps 22:04:56 vs the legacy
   build at 22:07).
4. The supported replacement for this installed version is `next analyze
   --output --snapshot …` (+ `next analyze export`), which produced a complete
   route/module graph as shown above.

Nothing was removed or changed in Phase 0; the config still exists as found.

## 4. Repeatable runtime scenarios — readiness only (no profiling run)

Stand state (**MEASURED**): the disposable compose project `reportcollector-ci`
is running and healthy — `front 127.0.0.1:16000`, `back 127.0.0.1:19000`,
postgres `127.0.0.1:16432`, valkey `127.0.0.1:17379`;
`GET http://127.0.0.1:16000/api/health` and `GET
http://127.0.0.1:19000/health` both returned 200. The back container has
`KEYCLOAK_ENABLED=false` and `VALKEY_URL=redis://valkey:6379/0`. The stock CI
compose (`infra/compose.ci.yaml`) publishes `15000/18000/15432/16379`; the
`16000/19000/16432/17379` mapping is an env override used for the recorded k6
runs (`docs/LOAD-BASELINE.md`, `docs/PRESENCE-FANOUT.md`, `load/README.md`).

Seed/fixture inventory (**MEASURED** / **STATIC**):

- `back/src/scripts/seed_database.py` creates deterministic demo rows and, only
  with `RC_SEED_E2E_FIXTURES=1` + `E2E_TEST_PASSWORD` + `KEYCLOAK_ENABLED=false`,
  the reserved `user1@test.local` … `user4@test.local` identities plus four
  work-coded demo plan rows and a preliminary archive snapshot.
- Current disposable DB counts: `rc.r_plan_rows` = **4**,
  `rc.rm_asset_raw_materials` = **67**, `rc.rm_material_movements` = **134**,
  `rc.app_users` = **24**.
- `docs/seed/` contains only `form_spec.xlsx` (348 KB), `id_spec.docx` (71 KB)
  and `Дополнения форм.docx` (494 KB) — all local/ignored. The ~94k-row,
  ~129 MB renovation workbook described in `.gitignore:53-57` is **absent**.
- `back/tests/benchmark_rollout_workload.py` can seed a synthetic **306k-row**
  plan into a dedicated `rc_workload*` schema (`--rows 306000 --reps 5`) and
  measure windows/facets/export; it is a **backend** harness, not a browser
  fixture, and was not run here.
- `k6.exe` is on PATH; `docs/LOAD-BASELINE.md` records v2.4.0.
- `E2E_TEST_PASSWORD` is **not** exported in this shell (and is not stored in
  the running back container); Playwright does not read `.env` files.

| Scenario | Readiness | Evidence / blocker |
|---|---|---|
| Open largest representative workspace (`/[project]`), switch sections/workspaces | READY, NOT RUN | `front/e2e/smoke.spec.ts`, `workspace-panels.spec.ts`, `raw-materials-ui.spec.ts`; stand healthy; needs `E2E_BASE_URL=http://127.0.0.1:16000` + `E2E_TEST_PASSWORD` |
| Initial grid render, vertical/horizontal scroll, sort/filter | READY, NOT RUN | `raw-materials-ui.spec.ts`, `workspace-panels.spec.ts`; no trace/FPS recording taken (Phase 0 rule) |
| Single-cell edit, range/paste/copy, undo/redo | READY, NOT RUN | `smoke.spec.ts:20` (single-cell edit + Ctrl+A disabled), `clipboard.spec.ts` (5 cases: sorted Excel paste, keyboard copy/paste, Russian layout, reference validation, toolbar buttons), `collab.spec.ts` (undo/redo propagation); seeded movement rows exist |
| Presence with multiple clients | READY, NOT RUN | `e2e/collab.spec.ts` (3 cases: asset-scoped presence, remote lease, two-tab takeover) and `k6 run load/presence-scale.js` with `RC_FRONT_URL=http://127.0.0.1:16000`, `RC_WS_URL=ws://127.0.0.1:19000/api/ws/raw-materials`; prior measured results in `docs/PRESENCE-FANOUT.md` are **HISTORICAL** for this phase |
| Menus, popovers, dialogs, fullscreen, reduced motion | READY, NOT RUN | `hover-dropdown.spec.ts`, `renovation-hover.spec.ts`, `smoke.spec.ts:5`, `workspace-panels.spec.ts` |
| ~94k-row renovation plan in a browser | **BLOCKED** | no committed/ local 94k workbook; disposable DB has 4 plan rows; `docs/TODO.md:472-503` says the large-plan e2e harness is deferred/not available. The 306k synthetic harness is backend-only. No runtime claim is made. |
| Heavy browser profiling (long tasks, scripting/layout/paint, INP, React Profiler, heap) | **BLOCKED in Phase 0 by task scope** | not attempted; no timings to report |

## 5. Bottom line for Phase 0

- All five canonical static checks are green on commit
  `f53ea9cf…`; E2E and browser profiling were deliberately not run.
- Route-level initial client JS is 631.8–776.9 KiB raw (194.2–241.5 KiB gzip);
  the aggregate 37-chunk bundle is 3,723.7 KiB / 1,186.5 KiB gzip, and the two
  largest chunks are async workspace/grid chunks, not initial-route payload.
- The Turbopack analyzer for this exact Next version works and produced a
  repeatable graph; the configured `@next/bundle-analyzer` produces no report
  for the real build and is dead/misleading configuration (left in place here).
- The dominant pre-minify client module is the RevoGrid Pro **trial** bundle,
  imported through four workspace/grid entry points; this is a static finding,
  not yet an optimization.
- Large-plan browser scenarios are blocked by missing fixtures; the small
  seeded stand (4 plan rows) is sufficient only for functional e2e, not for
  large-table performance claims.

Report path: `D:\Work\ReportCollector\docs\FRONTEND-AUDIT-BASELINE.md`
(untracked; not committed). Large analyzer artifacts remain outside the repo.
