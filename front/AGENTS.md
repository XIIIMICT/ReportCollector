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

## Working rules

- Use `bunx`, not `npx`; `uv` on the backend, not bare `python`.
- `git status` + branch before git operations; no commit/push without explicit
  request; UTF-8/no BOM/LF.
- `just dev` syncs a running stack via compose `--watch` — never `docker cp`.
- e2e needs a running prepared stack, `KEYCLOAK_ENABLED=false`, workers=1.
- **E2e identities and fixtures:** smoke specs resolve all credentials through
  `e2e/helpers/test-users.ts` — reserved `user1@test.local` … `user4@test.local`
  and the shared `E2E_TEST_PASSWORD` env var (placeholder in root
  `.env.example`). No corporate emails or password literals in specs. Playwright
  does not read `.env` files: export the env var in the shell. The isolated seed
  creates those users and the deterministic archive/work-code fixtures only with
  `RC_SEED_E2E_FIXTURES=1` (never on prod or with Keycloak enabled).
- There is ONE Playwright config: `front/playwright.config.ts` is used by both
  local and CI runs (there is no separate local config). It owns `testIgnore`,
  the `smoke` / `ui-keycloak` project names and the smoke spec selection; CI's
  discovery guard checks the same 10 spec basenames. Keep both lists in sync.
- From `front/`, `bunx --no-install playwright test --project=smoke --list`
  discovers the smoke suite without a running stack; CI logs it and fails if a
  required spec disappears. It is not a substitute for running the suite.
- **Docstrings:** D100–D107 are ignored in `back/pyproject.toml`; write one only
  for a non-obvious contract, never to satisfy a linter.
