<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Frontend rules (repo-level)

HeroUI v3 reference and Next-version notes are above. Cross-stack domain rules
live in `back/AGENTS.md`; contracts in `docs/API.md`, `docs/COLLAB.md`,
`docs/GRID.md`, `docs/DATA-JOBS.md`.

## Landmines

1. **revo-grid selection:** only `grid.setCellsFocus(start,end)` + `grid.refresh()`;
   `SelectionStore.setRange` is a dead API. Reset also needs
   `grid.clearFocus()` + `grid.refresh()` and
   `grid.pendingColumnFocusRestore = undefined`; clear a range BEFORE
   `setSelectedRowIds` (which rebuilds `columns`). Details — `docs/GRID.md`.
2. **Screen index ≠ `rows` index:** every index→row mapping (paste, cut, copy,
   presence, `canPasteRange`, shift-select, Ctrl+A) goes through
   `getVisibleRows()` = `grid.getVisibleSource("rgRow")`. Otherwise values land
   on the wrong row → fake deletions → FK RESTRICT.
3. **Excel paste:** trim trailing empty rows/columns (Excel adds `\n`) or an
   extra empty cell overwrites a neighbour.
4. **Hotkeys by `event.code`** (`KeyC`/`KeyV`/`KeyX`/`KeyA`/`KeyZ`/`KeyY`/`Delete`),
   not `event.key` — Russian layout breaks the latter. Ctrl+Delete opens the
   delete dialog. On «Ремонт» (~94k rows) **Ctrl+A is fully disabled**,
   including revo-grid's built-in select-all: a whole-form copy would overflow
   the clipboard.
5. **`afteredit` shape (revo-grid 4.32.0):** a plain single-cell edit carries
   `data` as an ARRAY; a range save (`rangeeditapply`) carries a keyed patch +
   `models`. `extractEdits` must distinguish by shape (keyed object AND no
   `prop`), not by `data` presence — otherwise cell edits silently do nothing.
6. **The document never scrolls:** `html` `overflow: hidden`, `body`
   `overflow: visible`, fixed `.app-shell: 100dvh` scrolling internally. Clip
   only `html`; `body: hidden` would cut off HeroUI portals (tooltips vanish).
7. **Streamed XLSX invariants** live in `back/src/common/xlsx_stream.py` (plain
   ZIP + append-only sinks); see `back/AGENTS.md` and `docs/GRID.md` «Misc».
8. **`r_plan_rows` uses semantic names** (migration 0041), not `col_NNN` —
   see `back/AGENTS.md` and `docs/GRID.md`.
9. **A11y:** a form control without a visible `Label` must have `aria-label`,
   or react-aria warns on every mount and floods `docker logs`.
10. **Styling:** recurring semantic UI — plain CSS classes in
    `front/src/app/globals.css` on theme vars; one-off layout/HeroUI slot
    overrides — Tailwind utilities inline.
11. **Workspace tabs:** main tables <splitter> dictionaries <splitter> users
    (`WorkspaceTab.separatorBefore`); «Логистика» starts with «Ставки».
12. **Presence deltas:** `presence:update` may carry `changes` instead of a full
    `members` map. `CollabWsClient` merges them into one authoritative map,
    ignores non-newer `seq` values and resets the sequence on every `hello` —
    do not replace a delta with `toMembers(message.members)` or drop the seq
    guard (duplicate/reordered frames would corrupt presence). Details —
    `docs/COLLAB.md`, `docs/PRESENCE-FANOUT.md`.
13. **Keyed hot paths stay keyed:** apply plan `cell:changed` values by inverting
    the received `table/rowId/field` keys once
    (`renovation-workspace-view.ts::remoteCellPatchesByRow`) and resolving them
    through the loaded-window mirror — never a loaded-rows × mergeable-fields
    scan per frame. Resolve row-header copy selections with one id map
    (`grid-clipboard.ts::rowsForSelectedIds`), not `rows.find` per selected id.
    Rationale, static complexity map and the BLOCKED large-plan profiling —
    `docs/FRONTEND-REFACTOR.md`, «Phase 2.2/2.4».
14. **Reduced motion:** every app-owned animation must become instant under
    `prefers-reduced-motion: reduce`; the policy lives in
    `src/app/styles/11-reduced-motion.css` (imported last). It also carries the
    targeted opt-out for HeroUI v3.2.8's popover/dropdown/tooltip/select/
    autocomplete enter/exit keyframes, which the library itself does NOT gate on
    reduced motion — never replace it with a blanket `animation: none`, and keep
    HeroUI's own `motion-reduce:*` rules for modals/toasts/fields intact. Do not
    tokenize the ~15 ad-hoc durations without a visual-regression comparison.

## Working rules

- Generated HTTP DTOs live in `src/shared/api/generated/openapi.d.ts` (from the
  in-process `app.openapi()` export, `bun run gen:openapi`; CI gate
  `bun run gen:openapi --check`). They are compile-time only — never hand-edit,
  never treat them as runtime validation. WS/collab and UI-only types stay
  handwritten. `pinned swagger-typescript-api` is dev-only with no TypeScript
  peer, so the TypeScript 7 app compiler needs no override.
- **Grid PATCH responses are JSON scalars:** `/api/raw-materials/movement` and
  `/api/raw-materials/prices` answer with the applied-cell count as a bare
  number. `requestWrite` parses object bodies only by default; those two callers
  pass `{ allowScalar: true }`. Never relax the default — the strict check is
  what rejects an HTML/malformed body returned with HTTP 200.
- Use `bunx`, not `npx`; `uv` on the backend, not bare `python`.
- `git status` + branch before git operations; no commit/push without explicit
  request; UTF-8/no BOM/LF.
- `just dev` syncs a running stack via compose `--watch` — never `docker cp`.
- e2e needs a running prepared stack, `KEYCLOAK_ENABLED=false`, workers=1. Local origin is the reverse proxy `https://127.0.0.1` (self-signed cert allowed in `playwright.config.ts`); CI overrides it with the isolated stand's `http://127.0.0.1:15000`. App ports 5000/8000 are not published on the host — see `docs/ARCHITECTURE.md`, «Published-port contract».
- **E2e identities and fixtures:** smoke specs resolve all credentials through
  `e2e/helpers/test-users.ts` — reserved `user1@test.local` … `user4@test.local`
  and the shared `E2E_TEST_PASSWORD` env var (placeholder in root
  `.env.example`). No corporate emails or password literals in specs. Playwright
  does not read `.env` files: export the env var in the shell. The isolated seed
  creates those users and the deterministic archive/work-code fixtures only with
  `RC_SEED_E2E_FIXTURES=1` (never on prod or with Keycloak enabled). The CI
  `front` container carries a synthetic `SUPPORT_MAIL` fixture so `.support-link`
  is visible there (contract — `docs/ARCHITECTURE.md`); e2e asserts its contract
  and never follows the `mailto:`.
- There is ONE Playwright config: `front/playwright.config.ts` is used by both
  local and CI runs (there is no separate local config). It owns `testIgnore`,
  the `smoke` / `ui-keycloak` project names and the smoke spec selection; CI's
  discovery guard checks the same 10 spec basenames. Keep both lists in sync.
- From `front/`, `bunx --no-install playwright test --project=smoke --list`
  discovers the smoke suite without a running stack; CI logs it and fails if a
  required spec disappears. It is not a substitute for running the suite.
- **Docstrings:** D100–D107 are ignored in `back/pyproject.toml`; write one only
  for a non-obvious contract, never to satisfy a linter.
