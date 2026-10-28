# Frontend audit — 2026-10-28

## Executive verdict

The frontend is functional, typed, and backed by a useful set of unit and smoke checks, but the grid layer has become a distributed state machine. React state, RevoGrid's internal stores, collaboration maps, imperative DOM overlays, timers, and browser-level event handlers all participate in selection, sorting, filtering, editing, and scrolling. The same lifecycle is implemented independently in the in-memory grid and the remote sparse plan grid. This is why a local fix often needs a second patch in another component and why a test can pass while a real grid interaction still regresses.

The highest-risk work is correctness first, then a stable RevoGrid boundary, then data and rendering performance. A replacement grid or a framework migration is not justified by this audit. The application already has valuable domain behaviour around visible-row coordinates, leases, Excel-compatible clipboard rules, session-scoped undo, and sparse plan loading. Those contracts should be made explicit and tested at the adapter boundary before any library decision.

Two defects were directly reproduced from production helpers before the Phase 0 changes:

- A renovation facet source reports a first 200-value window as the entire result. A 450-value source cannot be paged and a search match outside the first window is invisible.
- Range edit de-duplication compares every field as a number. A text change from `001` to `1`, or from `123abc` to `123xyz`, can be treated as unchanged; the grid can display a value that is never sent to the API.

The report started as an audit and Phase 0 now contains the targeted correctness changes described below. No dependency, database, seed, Compose file, or user CSS was changed. The performance measurements are in the companion [performance appendix](FRONTEND_PERFORMANCE_AUDIT_2026-10-28.md).

## Phase 0 status — completed

The Phase 0 gate is closed for the implemented paths:

- **F-01 closed.** Facet loaders now receive the search expression, the renovation API filters distinct values before applying `offset/limit`, and `total`/`rowCount` describe the current search scope. The popover uses a query generation, ignores late pages, and exposes a retryable error state.
- **F-02 closed.** Edit equality is descriptor-aware. Text, identifiers, references and selects use exact normalized strings; numbers use a strict full-string parser; arrays, booleans and dates have explicit comparisons. The renovation plan uses the same rule for range and single-cell edits.
- **F-09 facet portion closed.** Initial and tail facet failures are no longer reported as an exhausted list, and stale responses cannot mutate a newer query. The broader diagnostic/telemetry proposal remains open.
- **Regression coverage added.** The suite covers a 450-value search/paging source, late query responses, retry after failure, strict text equality, numeric equivalence and single-cell/range edit paths. Backend facet tests cover text and numeric filtering before pagination.

The remaining qualification is browser-level validation against the running authenticated stack: the unit and API tests prove the contracts, while a seeded browser run is still needed to validate RevoGrid event delivery, real network cancellation and production proxy behaviour.

## Scope, evidence, and assumptions

The audit covers `front/`, frontend CI/workflow configuration, the recent grid-related history, and the frontend sections of the repository workflow documentation. The inspected revision is `82a496c` on `dev`. The working tree already contained user changes in `compose.yaml` and `front/src/app/globals.css`; they were preserved.

Skill chain: the user-invoked `senior-frontend` skill (`C:/Users/Scorpi-ON/.agents/skills/senior-frontend/SKILL.md`), followed by its delegated `performance-profiler` specialist (`C:/Users/Scorpi-ON/.codex/skills/performance-profiler/SKILL.md`). The specialist produced the companion appendix; its findings were reconciled with this audit. Documentation is in English under the repository language policy.

Evidence is labelled as follows:

- **Confirmed** — reproduced with the production helper, observed in a direct source trace, or measured by a completed build.
- **Static risk** — the control flow is visible in source, but a browser or concurrent integration scenario was not run.
- **Proposal** — a design or acceptance target that still needs agreement and a baseline.

Priority P1 means address in the first reliability tranche because the finding can affect saved data, visible results, or regression protection. P2 means schedule after the correctness fixes or validate its runtime impact first. No release-wide P0 outage was established. A high priority does not turn a static risk into a browser-reproduced defect.

This is an authenticated corporate desktop application. The proposed performance profile is therefore desktop/corporate-network: p75 LCP ≤ 2,500 ms, INP ≤ 200 ms, CLS ≤ 0.10, WCAG 2.2 AA, Lighthouse performance ≥ 90, and accessibility ≥ 95. No named accessibility owner is assigned yet. These are acceptance targets, not measurements. A provisional budget is 200 KiB gzip for the authenticated shell and 80 KiB gzip for an ordinary route; the RevoGrid-heavy routes need a measured exception after a cold and warm browser trace.

These shared performance, bundle and accessibility guardrails apply to every recommendation below, in addition to its correctness acceptance criteria. The provisional budget does not imply a decision to change Next.js or the current rendering model.

No authenticated browser trace, Lighthouse run, production load test, Keycloak run, or end-to-end stack was started. Backend behaviour is treated as a dependency where a frontend fix needs a new API contract; this document does not claim to be a backend audit.

## Workflow and architecture map

`src/app` and the App Router authenticate and provision the current user. `features/report-workspace/report-workspace.tsx` selects one of three client workspaces through `next/dynamic`: raw materials, renovation, or KPPK. Each workspace owns toolbar, tabs, persistence, collaboration room, actions, and error/loading presentation.

There are two grid engines:

1. `shared/grid/data-grid.tsx` is the in-memory grid used by raw materials, KPPK dictionaries, and renovation dictionaries. It builds descriptors, manages visible-row mapping, sorting, facets, selection, clipboard, editors, leases, presence, export, geometry, and persistence.
2. `features/renovation/renovation-grid.tsx` uses RevoGrid Pro's `InfinityScrollPlugin` for a server-backed sparse plan. It owns chunk loading, index-to-row mapping, clipboard, autofill, custom eleven-level headers, scroll restoration, overlays, selection, and the same collaboration/editing concerns.

The largest orchestration units are evidence of change risk rather than a request to split files mechanically:

| Area                          | Approx. physical lines | Main concern                                                                      |
| ----------------------------- | ---------------------: | --------------------------------------------------------------------------------- |
| `renovation-grid.tsx`         |                  3,374 | remote source, clipboard, overlay header, geometry, selection and event lifecycle |
| `data-grid.tsx`               |                  3,343 | shared grid lifecycle plus in-memory facets and collaboration                     |
| `raw-materials-workspace.tsx` |                  2,087 | workspace, header/profile exports, tabs, actions and grid policy                  |
| `renovation-workspace.tsx`    |                  2,063 | plan scope, sparse row mirror, actions, import/export and dictionary routing      |
| `kppk-workspace.tsx`          |                  1,599 | four dictionaries, remote overlays, CRUD, undo and realization routing            |
| `column-filter-popover.tsx`   |                  1,038 | query state, paging, portals, selection and persistence                           |
| `grid-config.ts`              |                  1,029 | domain descriptors mixed with vendor columns, formatting and export               |

The useful existing seams should be retained: `grid-edit`, `grid-clipboard`, `react-editor`, `use-grid-clipboard-actions`, `history-action`, `use-table-export`, `section-policy`, and the common filter popover. The barrel `shared/grid/index.ts` also exports `grid-normalization`, `grid-selection`, `grid-utils`, `use-grid-selection`, `use-grid-column-filter`, and `use-optimistic-collab`, but most of these have no production consumers. `use-grid-selection` assumes numeric IDs and is incompatible with the UUID domain. Commit `696ed50` added 534 lines across five files without moving the actual grids to those abstractions. This is a warning against adding a third abstraction layer without a consumer and contract.

## Prioritized findings

### F-01 — Facet paging and search return an incomplete result (P1, confirmed)

The former implementation called a paged loader, filtered only that returned window in the browser, and returned `total: narrowed.length`. The renovation consumer also omitted the search expression. The implementation now forwards text/range/date search to `/plan/facets`; the backend narrows the complete distinct set before slicing the requested page.

The production helper was run against 450 synthetic values. It requested the first 200 values and reported `total=200`; the popover marked itself exhausted. Searching for the existing value `value-300` returned no value because it was not in the first window. The unfiltered size request is reported separately as `totalWithoutSearch`, so using that number blindly would still be wrong for a filtered query: the server must either filter before paging and return the filtered total, or expose an explicit cursor/`hasMore` contract.

**Impact:** users cannot select later distinct values and cannot find a value that sorts outside the first page. This is a user-visible correctness failure and can make a valid filter appear absent.

**Implemented:** the endpoint is search/range-aware and returns the filtered `total`; the source contract carries the query, and the popover uses a generation token plus a retryable error state. The component tests exercise 450 values, a match beyond page one, later pages, and late/out-of-order responses.

**Acceptance:** all values matching a query are reachable exactly once; a query change cancels or invalidates prior pages; the request log shows offsets/cursors advancing for the same query.

### F-02 — Field-insensitive edit comparison can silently lose text edits (P1, confirmed)

The former `front/src/shared/grid/grid-edit.ts` used `parseNumericText` for every descriptor. `Number.parseFloat` accepted numeric prefixes, so the range path could drop text updates. Equality is now selected by the column descriptor and the strict parser is shared by normalization and comparison.

Direct calls using the production helper produced no update for text `001 → 1` and `123abc → 123xyz`; `parseNumericText("123xyz")` produced `123`. Numeric display normalisation is used by the grids (`data-grid.tsx:1614-1624` and the corresponding renovation path), but the equality rule has no field kind.

**Impact:** a local RevoGrid value can differ from the saved value after a range edit or paste. A reload, collaboration event, or export then appears to “undo” a user edit.

**Implemented:** equality is descriptor-aware for single-cell and range edits, including text, numbers, arrays, booleans and dates. The tests cover `001`, `1`, `123abc`, `123xyz`, locale separators and single-cell edits.

**Acceptance:** every accepted visual edit either yields exactly one API update or an explicit rejected-cell message. Regression cases include `001`, `1`, `123abc`, locale spaces/commas, null, arrays, and date strings through the real edit event and paste path.

### F-03 — RevoGrid integration has no enforceable adapter boundary (P1, static risk)

The two grid components directly call vendor APIs and private implementation details. The focus escape is duplicated in `data-grid.tsx:1306-1321`, `renovation-grid.tsx:1052-1063`, and `shared/grid/grid-utils.ts:42-50`, including a cast to the private `pendingColumnFocusRestore`. The plan reads the private `revogr-temp-range.selectionStore` at `renovation-grid.tsx:2264-2291`. Sort state is simultaneously represented in React state, refs, local storage, column order, and plugin events (`data-grid.tsx:489-520`, `672-754`, `986-1145`, `2256+`, `2992-3132`).

The same component also owns global capture listeners, portal exceptions, polling, double-`requestAnimationFrame` measurements, and timers. `data-grid.tsx:2023-2111` retries header geometry up to 40 times at 100 ms. The plan has similar settled-loading polling in `renovation-grid.tsx:1660-1729`. These are reasonable responses to missing public events, but there is no versioned seam that isolates them from application state.

**Impact:** library upgrades and local fixes require synchronized edits in two monoliths. A stale timer or an event from the previous scope can mutate the next grid. Sort and selection feedback loops are difficult to reason about because the writer is not unique.

**Remediation:** define a narrow, versioned adapter with typed commands/events: `setRange`, `clearRange`, visible-row mapping, sort transition, clipboard dispatch, and overlay measurement. Keep plan-only sparse loading and eleven-level header rendering outside it. Hide private vendor access in one module and assert the installed vendor event shapes in integration tests. Use an epoch/cancellation token for every asynchronous geometry or scroll operation.

**Acceptance:** both grid implementations pass a shared adapter contract suite for selection, visible coordinates, clipboard and teardown, with capability-specific sorting cases. The sparse plan keeps its server-defined ordering unless sorting is explicitly supported. No feature component accesses `pendingColumnFocusRestore` or `selectionStore` directly.

### F-04 — KPPK remote overlays can replay stale values over a newer save (P1, static risk)

In `features/kppk/kppk-workspace.tsx:477-498` and `kppk-realization-section.tsx:311-335`, remote cell maps ignore the current user's echo and clear only on reconnect. Fresh rows are overlaid from the map (`kppk-workspace.tsx:790-808`, realization `:488-495`). A successful refresh after the current user saves does not reconcile or remove an older value from another user.

Scenario: another user writes A, the current user writes B, the GET response contains B, then the persistent map overlays A. The current user's echo is deliberately ignored, so the stale value remains until reconnect/remount or another remote event.

**Remediation:** make the row cache revision-aware. A successful mutation and every fresh read must reconcile `{rowId, field, revision}` and remove or supersede older overlays. Keep the distinction between remote optimistic overlays and canonical server rows explicit; do not solve this by indiscriminately clearing every map on any refresh.

**Acceptance:** two-user browser test: A → B on the same cell, reconnect, scope switch, and fresh GET all converge to the server revision without a stale flash.

### F-05 — KPPK scope changes keep old rows writable during loading (P1, static risk)

`shared/hooks/use-async-data.ts:13-63` intentionally retains prior `data` while dependencies change. `kppk-realization-section.tsx:270-282` ignores the hook's `loading` field and derives `isPending` from `view === null && error === null` (`:639`). The workspace key is only `selectedTab.id` (`kppk-workspace.tsx:1535-1541`), so organisation, month, year, and slice changes do not remount the section. The grid sets `readOnly={!isAdmin}` (`:678`) rather than disabling writes until the requested scope is loaded.

**Impact:** old rows can remain visible and editable while the toolbar describes a new scope. A write can combine an old row ID with the current slice/period. This is a race between user input and data identity, not just a loading spinner problem.

**Remediation:** key query state by a canonical scope key. Track `loadedScope`; keep old rows visible only when mutations are disabled and the scope badge says they are stale. Abort or ignore previous requests, and apply writes only when `loadedScope === requestedScope`.

**Acceptance:** browser test changes organisation/month/year/slice, immediately attempts an edit, and verifies no request is sent for the old row. The new scope becomes editable only after its rows and collaboration room agree on the same scope key.

### F-06 — KPPK merge formatting changes the data used for filtering (P1, static risk)

`kppk-realization-section.tsx:267` sets `mergesOff = isGridSorted || hasFilters`, yet `gridRows` at `:453-476` blanks product and excise values based on `showExcise`, `isGridSorted`, and group position. Covered values are still blank when a filter exists; `cellMerge` at `:517-539` is removed for filters. The grid therefore receives a presentation-shaped row model while filtering and sorting need canonical values.

**Impact:** filtering product or excise can discard sibling market rows, and a filter-only view can display or compare empty cells that exist in the server data.

**Remediation:** preserve canonical rows for filtering, sorting, export, collaboration, and edit mapping. Render covered cells as blank only in a cell template or merge presentation layer. Keep the merge operation as a view decoration, never a mutation of the row model.

**Acceptance:** the same dataset returns identical filter results and export values with merges on, merges off, sorted, and filtered; only the visual cell rendering changes.

### F-07 — Mutation ordering is not one protocol (P1, static risk)

Raw and renovation commits use `await pendingCommitRef.current; const task = doCommit(...); pendingCommitRef.current = task` (`renovation-workspace.tsx:1137-1151`, dictionary `:476-490`, and the raw equivalent). Two callbacks admitted before the next render can await the same old promise and then start together; this is not a queue with synchronous tail reservation. KPPK's `track` stores only the last started promise (`kppk-workspace.tsx:549-554`, realization `:345-349`), while realization paste expands an edit and performs sequential requests (`:403-425`). Undo waits for the last promise, not all writes.

**Impact:** out-of-order writes, a failure after partial KPPK paste, and an undo that observes only the last task. The sequential requests are intentional where a server lock/transaction requires order; replacing them with `Promise.all` would create a different failure mode.

**Remediation:** introduce one queue per immutable scope. Reserve the tail synchronously with a task factory, include edit/create/delete/replace/undo in the same order, return per-action outcomes, and invalidate the queue on scope change. Pair it with a future batched/atomic backend endpoint for KPPK paste rather than hiding partial success.

**Acceptance:** three deferred writes execute in input order, a failed middle write reports which cells committed, undo waits for the complete queue, and a scope switch cannot run the old queue against the new scope.

### F-08 — Remote sparse clipboard silently drops unloaded rows (P1, static risk)

`renovation-grid.tsx:2106-2123` skips absent rows while copying; `:2148-2211` skips absent rows while pasting and still selects the requested full range. The source is a bounded cache of InfinityScroll chunks, so a selection spanning an eviction or an unloaded chunk can collapse rows or silently save only a subset.

**Remediation:** before copy/paste, either fetch and materialise the complete range or reject it with a clear reason and a size limit. A range operation must report its actual row coverage; it must not silently continue with holes.

**Acceptance:** tests cross a chunk boundary, force eviction, and exercise copy, paste, autofill, and retry. The UI reports “range not loaded” or completes with all requested coordinates.

### F-09 — Query and asynchronous lifecycle errors are hidden (P2, partially closed)

Facet requests now carry a query generation. A late page is ignored after the search changes, and a failed initial or tail request remains retryable instead of being marked exhausted. Network abort signals and the broader `use-async-data`/mutation telemetry proposal remain open.

`GridErrorBoundary` only logs to `console.error` (`shared/ui/grid-error-boundary.tsx:21-31`), and async mutation failures are often toast-only. There is no structured client diagnostic for scope, action, revision, or interaction type.

**Remediation:** use a query controller with `{scopeKey, generation, status, error, retry}` and `AbortController` where the API supports it. Add privacy-preserving error/interaction telemetry and browser assertions for `pageerror` and `unhandledrejection`; never include cell contents or credentials.

**Acceptance:** old pages cannot mutate a new query, retry recovers a failed page, and a production diagnostic identifies the grid, scope, action, and vendor event without data values.

### F-10 — Large-array and heartbeat work is repeated on normal updates (P2, measured/static)

`data-grid.tsx:545` clones every row into `rowValuesRef` whenever `rows` identity changes. Facets and visible rows scan complete arrays at `:890-932`. The companion performance audit measured `computeRowFacets` on 50,000 rows at a median of approximately 43 ms over seven runs on this workstation, before React or RevoGrid work. `use-remote-cell-maps.ts:25-37` fingerprints every rebuilt map with `JSON.stringify([...map.entries()])`, which is O(remote markers) allocation on each collaboration heartbeat.

**Remediation:** use patch/versioned row maps and indexed facet values for large dictionaries. Have collaboration rooms expose a revision or deterministic incremental hash, preserving the current identity-stabilisation benefit. Add 1k/10k marker and 50k/300k row benchmarks.

**Acceptance:** unchanged collaboration heartbeats do not rebuild columns; one changed cell does not clone the entire dictionary; representative filtering meets the shared INP target and has a recorded CPU/heap baseline. Do not add a full-dataset index for the sparse plan unless its memory budget permits it.

### F-11 — Route splitting is weakened by importing a workspace as a shared UI module (P2, measured)

`report-workspace.tsx:20-34` correctly uses dynamic project imports, but KPPK and renovation import `AppHeader` from `features/raw-materials/raw-materials-workspace.tsx` (`kppk-workspace.tsx:15-18`, `renovation-workspace.tsx:18-23`). The raw workspace is 2,087 lines and its shared chunk appears in all three route graphs. Extract header/profile and other project-neutral UI into small modules with no workspace imports.

The measured chunk and route baseline, including the RevoGrid Pro chunk, is documented in the companion performance audit. Treat the route sums as emitted-graph estimates, not as a cold transfer measurement, until a real browser trace is available.

**Acceptance:** KPPK and renovation no longer runtime-import the raw-materials workspace module. Rebuild and compare module membership and cold/warm network traces; shared grid code may remain shared, so do not promise removal of the whole 195 KiB chunk.

### F-12 — Tests overstate UI protection in key integration paths (P1, confirmed coverage gap)

At the initial audit baseline the local suite passed 344 tests across 63 files; after Phase 0 it passes 352. Coverage includes 60 files and 5,832 executable lines, so the 70.78% gate still does not mean the core grid/workspace files are covered. `grid-outside-click.test.ts` defines a local `outsideClickVerdict` instead of importing the handler. `plan-scroll-restore.test.ts` defines a local `gate` and checks a hand-built selector rather than mounting the renovation grid. `report-workspace.test.tsx` mocks all workspace implementations. These tests protect a specification idea, not necessarily the production wiring.

The E2E suite has nine smoke specs and CI runs the smoke project, which is a strength. There is no KPPK realization interaction spec for scope switching, merging/filtering, stale overlay reconciliation, or multi-cell paste. There are also no real adapter tests for >200 facets, sparse chunk boundaries, native mouse/keyboard sorting, or concurrent mutation failures.

**Remediation:** keep pure tests for pure functions, but add a small number of mounted tests at the real seam: actual grid event dispatch, real handler registration/teardown, current vendor event payloads, and a seeded authenticated browser scenario for each high-risk workflow. Prefer deterministic deferred promises and fake timers over fixed 300/800/1,500 ms sleeps.

**Acceptance:** deliberately remove the production outside-click handler or break the production scroll-restore condition and the corresponding test fails. Every critical grid behaviour has at least one test reaching its real implementation. Report the files absent from coverage and remove fixture-dependent skips from the required isolated smoke run.

### F-13 — RevoGrid dependency contract is undocumented and internally inconsistent (P2, static risk)

`package.json` declares `@revolist/react-datagrid` 4.29.1 while its dependency declares RevoGrid 4.29.1; the project override and lockfile install `@revolist/revogrid` 4.28.4. `rv-pro-trial` accepts `revogrid ^4.28.0`. Repository guidance and `docs/GRID.md` still describe 4.27 event shapes. The mismatch is not evidence that a specific defect came from the version override, but it is an uncontrolled compatibility surface.

**Remediation:** record the exact installed matrix (React wrapper, core, Pro plugin), the event payloads relied upon, and the supported browser set. Add a CI smoke that imports the installed packages and exercises `afteredit`, `setrange`, `focuscell`, sorting, `InfinityScrollPlugin`, and teardown. Upgrade only with a contract diff and representative browser runs.

**Acceptance:** the lockfile, installed-package manifest and compatibility document agree; the documented supported combination passes both grid contract suites. Pinning a version is a prerequisite, not proof of compatibility.

### F-14 — CSS and private vendor markup form a second integration API (P2, static risk)

`front/src/app/globals.css` is approximately 3,660 lines with 89 `!important` occurrences in the current working tree. Grid geometry depends on internal tags/classes such as `revogr-header`, `revogr-data`, `rgHeaderCell`, `revogr-scroll-virtual`, and inline vendor dimensions. This is understandable for the required Excel-like shell, but it couples upgrades to undocumented DOM shape and makes unrelated layout changes risky.

**Remediation:** put vendor selectors in one versioned adapter stylesheet, document each selector and the vendor version it targets, and expose app-level geometry through data attributes or wrapper classes. Remove a selector only with a browser regression for pinned/unpinned headers, native scrollbar lanes, overlays, dark theme, and the no-document-scroll rule.

**Acceptance:** the representative layout snapshots and focus/portal interactions pass in light/dark themes, both row densities, fullscreen and normal layouts, and 100%/125%/150% scaling. All private vendor selectors have one owner and a linked regression scenario.

## Cross-cutting frontend review

**API boundaries.** `shared/api/api-error.ts:42-75` already centralises HTTP error parsing, `no-store` and write bodies, which is worth retaining. Successful responses are cast with `as T`; most grid data is not runtime-validated. The helper also has no `AbortSignal` parameter. Validate high-value envelopes (page identity, row IDs, revisions, metadata/descriptor shape) and carry cancellation through reads. Prefer generated API types plus focused runtime boundary checks to parsing every cell repeatedly in a 200-column dataset. A malformed page should produce a useful load error, not propagate `undefined` through a grid cast. Move neutral `CellUpdate` and scope types out of the raw-materials API module when extracting the adapter.

**Persistence.** The existing project/section storage keys, malformed-JSON guards and stable row IDs are good foundations. Preserve their semantics. Add schema versions, explicit scope normalisation, allowed-column checks and a retention limit for scroll positions keyed by arbitrary filter signatures. Decide explicitly which view preferences are shared between users on one browser. Test removed columns, old schema, quota failure, filter/sort reset and a deleted remembered row; this audit did not establish a cross-user access-control defect.

**Workspace publication.** Child sections publish toolbar/clipboard/export/undo state back to parents. The project documents prior maximum-update-depth loops, and existing stable callbacks in `use-table-export` and equality guards are useful fixes. Give each published action one stable controller and test a rerender with changing inputs. An extraction is complete only after its real consumers move and old inline copies are deleted; merely adding more hooks repeats `696ed50`.

**Hook and stats correctness.** `data-grid.tsx:2186-2238` omits `sortedColumnPaths`/`filteredColumnPaths` from `measureViewport` dependencies, and its equality guard compares filtered-column count without comparing the paths. Switching between same-sized filter sets can leave a stale status label. Add a regression that changes the filtered column without changing counts. The lint configuration disables several compiler-related rules globally; review exceptions at the vendor boundary and enforce meaningful dependency checks after their current failures are resolved. Do not mass-add `useCallback` to satisfy performance hints.

**UI and accessibility scope.** Shared controls generally provide Russian labels, real HeroUI primitives and status/error roles. The audit did not perform a WCAG or screen-reader assessment. Keyboard ownership across a grid editor, a body portal and a dialog needs browser coverage because capture listeners can suppress each other. Test focus return, Escape ownership and clipboard shortcuts with actual pointer/keyboard input, not only `setCellEdit` calls. The grid-specific boundary still renders English “Grid Error”/“Retry”, unlike the application's Russian UI; align its recovery message with the shared error UI. KPPK realization and renovation dictionaries do not have the same local grid boundary as other sections, although the application-level Next boundaries still exist.

**CI and operational workflow.** Existing CI builds production containers and runs a seeded Chromium smoke against those same images; it is not limited to unit tests. The improvement is scenario coverage and reliable fixtures, not starting a second CI system. Add a small mounted vendor-contract layer and authenticated performance job to the existing pipeline. Unit output currently contains `act(...)` and PressResponder warnings; remove known fixture warnings and make unexpected browser errors fail tests. Keep Keycloak, production proxy and large workbook checks explicitly separate from the PR smoke result.

## History signal

The recent history supports the systemic diagnosis. At the audited HEAD, `git log --format=%H --since=2026-10-24T00:00:00+03:00 -- front/src/shared/grid front/src/features/renovation/renovation-grid.tsx front/src/app/globals.css` returns 108 commits. This is a change-frequency signal, not a defect count; the explicit midnight matters because Git otherwise retains the current time of day for a date-only cutoff.

The sorting persistence series starts at `f2493d1` and is followed by `4f465d5`, `738998e`, `b62e8eb`, `6b734bd`, `6538fc1`, `c318190`, `0ec15c6`, `09fb0a7`, `4d34537`, `ded5a65`, `fad0318`, and `65dae7f`. These commits repeatedly move authority between local storage, column state, refs, and plugin callbacks. Outside-click selection needed `414ce2f` in the shared grid and then `a94f6eb` in the plan. Header filter and overlay behaviour was rewritten through a chain including `e8d6153`, `cfbf850`, `eb47475`, `49f9ed2`, `8c545eb`, `b7e3ae3`, `4bf0bd8`, `b12add9`, and `5376778`.

This history does not mean the fixes were unnecessary. It shows that the current change boundary is too wide: one behaviour is duplicated or controlled by several authorities. The appropriate response is an executable contract and a single owner for each transition, not another one-off patch.

## Target architecture

The desired shape is incremental:

1. **Domain model.** Canonical rows, descriptors, field kinds, revisions, and scope keys remain independent of RevoGrid. Filtering, sorting, export, collaboration, and edit comparison consume this model.
2. **Data ports.** One port supplies an in-memory visible window; another supplies remote chunks and explicit completeness. Both expose the same visible-coordinate and patch contracts.
3. **Grid adapter.** One small module owns installed-vendor event translation, range commands, teardown, and the few unavoidable private selectors. Feature code receives typed events and never reaches into a vendor store.
4. **Interaction controllers.** Selection/clipboard, mutation queue, query paging, collaboration reconciliation, and geometry each have one owner. They use scope epochs and cancellation rather than timers that can outlive a scope.
5. **Views.** Raw, KPPK, and renovation components provide descriptors, policy, and plan-specific presentation. Merges and overlays decorate canonical data; they do not rewrite it.

Do not create a universal grid with dozens of mode flags. The shared in-memory grid and sparse renovation plan have genuinely different data-source semantics. Share contracts and lifecycle primitives, then keep plan-specific loading and header presentation separate.

## Remediation sequence and gates

### Phase 0 — correctness and observability

Phase 0 is complete for F-01, F-02, and the facet portion of F-09. The implementation defines field-aware equality and a server-side facet query contract, adds popover/edit/paste regressions, and uses request generations with safe retryable async states. Action IDs, broader scope-key observability, and mutation telemetry remain Phase 1/2 work.

**Gate:** all confirmed reproductions pass; the 450-value facet query, text-prefix edit, single-cell edit, reload, retry, and late-response tests are green. Browser/E2E validation is still outstanding.

### Phase 1 — adapter and lifecycle contracts

**Implementation status: code and unit-contract portion complete; browser gate remains open.**

`revo-grid-adapter.ts` now owns the shared selection commands, visible/source
reads, range-edit dispatch, shadow-DOM target detection, temporary drag-range
subscription, and the two unavoidable private RevoGrid accesses. Both grid
engines use it for those paths. The sparse plan rejects copy/paste ranges with
unloaded rows instead of silently dropping them. The adapter contract suite
covers selection refresh/cleanup, disposed-grid fallbacks, event dispatch,
temporary-range teardown, sparse coverage and scope epochs. The installed
package/event matrix is recorded in [REVOGRID_CONTRACT.md](REVOGRID_CONTRACT.md).

This closes the implementation portion of **F-03** and **F-08**, and the
contract/documentation portion of **F-13**. **F-12** now has a real adapter
contract suite, while the mounted/browser coverage it calls for is still open.

Sorting and InfinityScroll loading remain capability-specific by design. The
real browser gate is still outstanding: it must exercise shadow-DOM focus,
native clipboard, mouse/keyboard sorting, drag selection, chunk eviction and
teardown against the running authenticated stack.

**Gate:** the shared adapter contract suite is green; the browser portion is
pending the seeded authenticated run described above.

### Phase 2 — canonical data and collaboration

Implement scope-keyed query state (F-05), canonical KPPK overlays (F-04/F-06), and a true per-scope mutation queue (F-07). Specify the backend dependency for atomic KPPK batch paste; until then, surface partial outcomes accurately.

**Gate:** two-user conflict, reconnect, scope switch during load/write, filter/merge/export equivalence, and failure/undo tests converge on server revisions.

### Phase 3 — performance and bundle boundaries

Extract neutral header/profile modules (F-11), establish a Turbopack-compatible size report, and capture cold/warm authenticated Chrome traces. Index/window large facets and replace repeated full-row clones and JSON heartbeats (F-10). Coalesce header measurements into one settled/observer path.

**Gate:** p75 targets and route budgets are measured on a fixed desktop profile; any grid exception is documented with transfer, long-task, heap, and scroll-FPS evidence.

### Phase 4 — CSS and maintenance hardening

Move private vendor selectors into a documented adapter stylesheet (F-14), remove dead abstractions only after consumer search, and raise the test floor for the core grid/workspace files rather than only increasing a global percentage.

**Gate:** the installed vendor upgrade checklist, browser matrix, no-document-scroll rule, dark theme, portals, and pinned/unpinned layout all have repeatable checks.

## Regression matrix

| Surface        | Required cases                                                                                                                                                        |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local grid     | single/range edit, strict numeric/text equality, sorted and filtered visible coordinates, row selection clearing range, copy/cut/paste, undo/redo, add/delete/replace |
| Sparse plan    | chunk load/eviction, copy/paste across boundaries, autofill drag, scroll restore, overlay header resize, pinned/unpinned shell, 300k-row scope                        |
| KPPK           | org/month/year/slice switch, stale data disabled during load, merge/filter/export equivalence, excise fan-out, paste failure, undo after queued writes                |
| Collaboration  | own echo, other-user edit, same-cell conflict, lease/presence, reconnect, stale overlay reconciliation, revision convergence                                          |
| Filters        | 450 values, late page, query change, numeric/date ranges, null bucket, retry after network failure                                                                    |
| Browser input  | Russian keyboard layout via `event.code`, native mouse header sorting, shadow-DOM editor focus, portal click, Escape, Ctrl+A/Delete, clipboard permissions            |
| Release checks | typecheck, lint with warning/error budget, format, unit/coverage, production build, seeded smoke, authenticated performance trace                                     |

## Validation performed

| Check                                                        | Result  | Meaning                                                                                                                                                              |
| ------------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bun run --cwd front typecheck`                              | pass    | 204 files, no type errors                                                                                                                                            |
| `bun run --cwd front test`                                   | pass    | 359 tests / 64 files                                                                                                                                                 |
| `bun run --cwd front test:coverage`                          | pass    | 70.94% of 5,956 reported lines; 65% floor                                                                                                                            |
| `bun run --cwd front format:check`                           | pass    | 208 files                                                                                                                                                            |
| `bun run --cwd front lint:check`                             | pass    | unused `ExportMode` import removed in `features/kppk/kppk-workspace.tsx:16`; 472 existing warning lines remain, mostly React Compiler-aware `react-perf` hints |
| `bunx --no-install stylelint`                                | pass    | stylesheet rules pass when run separately                                                                                                                            |
| `bun run build`                                              | pass    | Turbopack production build; approximately 119 s on this workstation (specialist run)                                                                                 |
| `uv run --directory back ruff check` / `ruff format --check` | pass    | read-only repository delivery checks; cache-write warning, 166 files formatted                                                                                       |
| `uv run --directory back ty check`                           | fail    | existing `Decimal \| None` passed to `float` in `tests/test_kppk_periods.py:121`; outside frontend scope                                                             |
| `uv run --directory back pytest -q tests/test_renovation_api.py` | pass | 87 tests, including server-side facet search and date/range semantics                                                                                              |
| Backend full suite                                           | not run | only the renovation API slice was needed for the Phase 0 contract                                                                                                    |
| E2E                                                          | not run | requires the isolated seeded stack                                                                                                                                   |

The React Compiler is enabled (`reactCompiler: true`), so the large `react-perf` warning count is not proof that every inline function or object is a defect. The lint command now passes after removing the unused type; warnings still need triage before making lint a stricter budget. The coverage number excludes the most important grid/workspace files from the reported set and should not be used as whole-frontend confidence.

## Limitations and next evidence

The audit did not measure real-user CWV, authenticated route transfer, RevoGrid frame time, heap growth, screen-reader behaviour, or concurrent browser writes. It also did not test Keycloak, production nginx, or a full 300k-row installation. The next useful step is a deterministic seeded browser harness that records cold/warm traces and runs the regression matrix above. That evidence should decide budgets and whether the adapter can continue to support the installed RevoGrid version; it should not be assumed that swapping the grid fixes application-level data and collaboration races.
