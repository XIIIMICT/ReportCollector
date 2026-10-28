# Frontend performance audit — 2026-10-28

## Scope and assumptions

This is a read-only audit of `front/` and the frontend-related build output. The application is treated as an authenticated corporate desktop application. The project does not currently provide a named accessibility owner or a production browser trace, so the following are proposed acceptance targets rather than measurements: LCP ≤ 2,500 ms, INP ≤ 200 ms, CLS ≤ 0.10 at p75 on the primary desktop profile; WCAG 2.2 AA; Lighthouse performance ≥ 90 and accessibility ≥ 95. A provisional JavaScript budget is 200 KiB gzip for the authenticated shell and 80 KiB gzip per ordinary route. RevoGrid-heavy routes need a separately agreed budget after a browser trace.

The audit deliberately did not start Docker, the database, a seed, or an end-to-end stack. Existing user changes in `compose.yaml` and `front/src/app/globals.css` were left untouched.

This appendix was produced through the `senior-frontend` → `performance-profiler` skill chain and reviewed together with the [main frontend audit](FRONTEND_AUDIT_2026-10-28.md). Its shared performance/accessibility targets apply to every recommendation; the main report defines correctness gates and the remediation order.

## Reproducible baseline

`bun run build` in `front/` completed successfully with Next 16.4.3/Turbopack. The build reported 119 s for optimized compilation, followed by a successful TypeScript pass and static-page generation. This is a build-time baseline on one Windows workstation, not a CI or production latency target.

The emitted client chunks contain 3,475.90 KiB raw JavaScript and 1,117.36 KiB gzip (32 JS files), plus 934.59 KiB raw CSS and 121.73 KiB gzip (2 CSS files). Sizes were measured from `.next/static/chunks` after the build with PowerShell byte reads and an in-memory `.NET GZipStream` at optimal compression. They are emitted-artifact sizes, not network transfer sizes. The largest files are:

| Artifact | Raw | gzip | Interpretation |
| --- | ---: | ---: | --- |
| RevoGrid/Pro-heavy chunk `0vy9hiar-_-im.js` | 1,459 KiB | 488 KiB | Pro grid/plugin runtime; contains `InfinityScrollPlugin`/RevoGrid symbols |
| shared chunk `2r2ix4xdxkmpa.js` | 637 KiB | 195 KiB | Contains `RawMaterialsWorkspace`; it is included by KPPK and renovation load graphs |
| `2nii411saqstq.js` | 202 KiB | 65 KiB | shared runtime/vendor code |
| global CSS `1izjsqp1bu1_4.css` | 597 KiB | 72 KiB | Tailwind/HeroUI/global stylesheet |

The dynamic-import manifest entries in `.next/server/app/[project]/page/react-loadable-manifest.json` sum to approximately 254 KiB gzip for raw materials, 755 KiB for KPPK, and 775 KiB for renovation. These are partial emitted-chunk graph sums, not complete route payloads or measured transfers; shared entrypoints, transitively requested chunks, HTTP compression and cache state still need a browser trace. KPPK and renovation import `AppHeader` from `features/raw-materials/raw-materials-workspace.tsx` (KPPK lines 15–18; renovation lines 18–23); the emitted graph includes that workspace's 195 KiB gzip shared chunk. `ProfileModal` is imported from a separate module in the same feature directory.

For these sums, JS paths were de-duplicated within each manifest entry: `91057` (raw, six JS files, 254.26 KiB), `53801` (KPPK, eight, 755.44 KiB), `77535` (renovation, eight, 775.29 KiB). CSS was excluded. The raw entry does not include the Pro-heavy chunk because raw materials does not import the Pro package; core RevoGrid code still appears elsewhere in its graph. Common shell/bootstrap files are excluded from these entry sums.

The `analyze` script is not currently a reliable shipped-bundle report: `@next/bundle-analyzer` explicitly returns without a report when `TURBOPACK` is set, while `next build` uses Turbopack. A webpack-only analyzer run would describe a different build. Establish a report compatible with the installed Turbopack build before using route bundle budgets as a gate.

## Findings

### P1 — facet paging reports a window as the full result

`front/src/shared/ui/column-filter-facets.ts:155-238` implements `serverFacetSource`. It loads one backend window, filters that window in memory, and returns `total: narrowed.length`; `totalWithoutSearch` is fetched separately and is not used for the loaded result count. `front/src/shared/ui/column-filter-popover.tsx:399` marks paging exhausted when `loaded.length >= facets.total`. A 450-value source therefore reports a first 200-row page with `total=200`, and the popover stops. A search for a value outside the first page also returns no match. This was reproduced directly with the production TypeScript module: first page `{loaded:200,total:200,totalWithoutSearch:450}`, and a query matching only item 449 returned `{loaded:0,total:0}`.

Users cannot reach later facet windows. The source contract must filter before paging and return the total for that query, or explicitly track a source cursor and completion while searching additional windows. Changing `total` to the unfiltered `totalWithoutSearch` alone does not repair search or offset advancement. Add a regression test with >200 values and a match beyond page one before changing implementation.

### P2 — RevoGrid payload dominates emitted grid JavaScript

The Pro-heavy chunk is approximately 488 KiB gzip. The KPPK and renovation dynamic-import entries sum to approximately 755–775 KiB gzip before measuring actual transfer. That puts pressure on the provisional ordinary-route budget; a grid exception needs a measured baseline. `report-workspace.tsx:20-34` correctly uses dynamic project imports, but the `AppHeader` import crosses back into the raw-materials workspace. Extract neutral UI into a small shared module, then rebuild and compare both the import graph and cold/warm transfer; the entire shared chunk is not necessarily removable because it also contains common grid code.

Do not attempt to split RevoGrid blindly: `data-grid.tsx` and `renovation-grid.tsx` rely on custom editors, shadow-DOM event handling, selection stores, and Pro plugins. Measure a real cold navigation with Chrome performance/coverage first, then choose between route-level preloading, a smaller shared adapter, or accepting a documented grid exception.

### P1 — shared editable grid is a 3,343-line orchestration boundary

`front/src/shared/grid/data-grid.tsx` combines column construction, sorting state synchronization, filter geometry, row selection, clipboard, leases/presence, editing, scroll restoration, export, and RevoGrid event wiring. `front/src/features/renovation/renovation-grid.tsx` is 3,374 lines and duplicates much of the same selection, clipboard, focus clearing, remote-cell, and funnel/scroll lifecycle. The duplication is visible in the parallel `setCellsFocus`/`clearFocus`/`refresh` paths and the repeated `pendingColumnFocusRestore` escape hatch. This is the main maintainability risk: a RevoGrid behavior change requires synchronized fixes in two implementations.

Create a narrow grid adapter layer with shared, unit-tested primitives for (1) visible-row coordinate mapping, (2) selection lifecycle, (3) clipboard/range edit dispatch, and (4) remote-cell overlays. Keep the plan-specific InfinityScroll source, custom header, summary and presentation behaviour separate. The refactor should be incremental and validated against real clipboard/selection/collaboration interactions, not just copied handler logic.

### P2 — whole-array work recurs on shared-grid updates

`data-grid.tsx:545` clones every row into `rowValuesRef` on every `rows` identity change. `data-grid.tsx:890-913` builds facets by scanning real rows, narrowing them by the other active column filters when needed. `data-grid.tsx:925-932` separately filters visible rows. Changed row/filter inputs can repeat scans; the cost depends on the number of rows, active filters and distinct values. A direct helper benchmark took a median ~43.28 ms for 50,000 synthetic rows on this workstation (seven iterations, searching `002`; maximum ~51.00 ms), before React/RevoGrid rendering. The rows used unique descending text names and integer values; there was no warm-up. This is a helper benchmark, not an end-to-end frame time or proof of current production row counts.

Prefer server-side/windowed facets for large dictionaries, or maintain normalized indexes keyed by column/value and update them from row patches. Avoid cloning the complete row set merely to support stale-event handlers; use a versioned row map or immutable patch log with bounded snapshots.

### P2 — remote-map stabilization uses repeated JSON serialization

`shared/collab/use-remote-cell-maps.ts:25-37` fingerprints every rebuilt lock/presence map with `JSON.stringify([...map.entries()])`. This avoids unnecessary grid column rebuilds (a good goal), but it is O(number of remote cells) CPU and allocation work on each heartbeat. With broad selections, the fingerprint itself can become the heartbeat cost and it also depends on insertion order. Replace it with a deterministic revision/version from the collaboration room, or an incremental stable hash with a size/revision pair. Keep the existing identity-stabilization behavior and add a benchmark for 1k/10k cell markers.

### P2 — header-funnel geometry has timer and layout work

`data-grid.tsx:2023-2111` measures every header cell with `getBoundingClientRect`, schedules two animation frames, and retries up to 40 times at 100 ms after a column change. `onViewportscroll` also schedules the measurement (`data-grid.tsx:3160-3185`). The equality guard prevents a render loop, but the repeated layout reads can compete with scrolling on a wide grid. Replace the retry loop with a single settled signal from the grid (or a bounded `ResizeObserver`/`MutationObserver` strategy), and coalesce scroll work through one RAF scheduler that drops stale requests.

### P2 — refreshes still refetch entire dictionary scopes after edits

The shared workspaces call `refresh()` after commits (for example `raw-materials-workspace.tsx:738-798`), which increments the loader key and re-reads the active table. Renovation deliberately patches loaded plan rows in place (`renovation-workspace.tsx:930-1010`), so it avoids viewport eviction; the shared dictionary grid does not have the same patch path. A future edit should reconcile the acknowledged patch into the local row store and re-fetch only catalog dependencies, reserving full reloads for server-side derived fields or rejected writes.

### P2 — no browser-level performance gates exist

CI runs frontend typecheck/lint/unit/coverage/e2e, but `.github/workflows/ci.yaml` has no Lighthouse, Chrome trace, Web Vitals, route bundle-size, or long-task gate. Unit tests cover many RevoGrid contracts, but they cannot detect a 500 ms commit task, a 1 MiB route transfer, or a 60 FPS scroll regression. Add a separate authenticated performance job with seeded fixture data and fixed browser/network/device settings. Store a baseline trace and fail only on a documented regression threshold.

## Recommended order

1. Fix and regression-test facet paging; this is a user-visible correctness defect.
2. Split neutral header/profile modules from the raw-materials workspace and establish a Turbopack-compatible route-size report.
3. Capture cold/warm Chrome traces for raw materials, KPPK, and renovation with realistic row counts; measure LCP/INP/CLS, long tasks, heap, and scroll FPS.
4. Extract shared grid lifecycle primitives, then move large-dictionary facets and row snapshots to indexed/windowed data paths.
5. Coalesce funnel geometry work and replace heartbeat JSON fingerprints with collaboration revisions.
6. Add performance budgets and an authenticated browser trace gate after the baseline is stable.

## Validation performed

- `bun run build` — passed; Turbopack build 119 s, TypeScript and static generation passed.
- Direct facet-source reproduction — confirmed the first-page total/paging defect described above.
- Helper benchmark — `computeRowFacets` + text search, 50,000 synthetic rows: median 43.28 ms, maximum 51.00 ms across seven samples on this workstation; no warm-up, not an end-to-end frame measurement.
- No Docker, database, seed, or e2e stack was started.
