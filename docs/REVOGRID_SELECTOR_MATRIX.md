# RevoGrid selector matrix

This is the inventory for the installed RevoGrid matrix (`@revolist/revogrid`
4.28.4, `@revolist/react-datagrid` 4.29.1, `@revolist/rv-pro-trial` 2.9.6).
It exists so a vendor upgrade has a finite review surface. New vendor DOM
selectors require an entry here and a browser regression before they are used.

## Ownership

| Selector or field | Kind | Current owner | Required regression |
| --- | --- | --- | --- |
| `.revogrid-license-info` | vendor chrome | `shared/grid/revo-grid-adapter.css` | authenticated route has no trial banner |
| `revogr-attribution` | vendor chrome | user-owned `app/globals.css` (preserved) | attribution policy / route render |
| `revogr-scroll-virtual.vertical`, `.horizontal` | internal scrollbar hosts | `app/globals.css`, scoped by `.revo-grid-shell` | pinned/unpinned scrollbar lane and no document scroll |
| `revogr-header`, `.header-rgRow`, `.group-rgRow`, `.rgHeaderCell` | shadow header DOM | `app/globals.css`, scoped by `.data-grid` | header height, sort/funnel, dark theme |
| `revogr-data`, `.rgRow`, `.rgCell` | shadow data DOM | `app/globals.css`, scoped by `.data-grid`/`.renovation-grid` | selection, borders, pinned totals, row density |
| `revogr-row-headers` | shadow row-header DOM | `app/globals.css`, scoped by grid wrapper | row-number alignment and pinned header |
| `revogr-filter-panel`, `revo-dropdown` | vendor filter portal DOM | `app/globals.css`, scoped by `.data-grid` | portal click, light/dark filter panel |
| `revogr-edit` | vendor editor host | `app/globals.css`, scoped by grid wrapper | editor portal, Escape, autocomplete/date editor |
| `revogr-temp-range.selectionStore` | private runtime store | `shared/grid/revo-grid-adapter.ts` only | autofill range subscription and teardown |
| `pendingColumnFocusRestore` | private runtime field | `shared/grid/revo-grid-adapter.ts` only | clear selection after column rebuild |

The last two entries are JavaScript implementation details and must never be
read from a feature grid. The adapter contract tests assert their current
shape. CSS selectors remain in the global stylesheet where they need to style
shadow-host descendants; the adapter stylesheet is the dedicated home for
standalone vendor chrome and is the migration target for future CSS moves.

## Upgrade checklist

1. Record the wrapper, core and Pro versions in `REVOGRID_CONTRACT.md`.
2. Run `bun test src/shared/grid/__tests__/revo-grid-adapter.test.ts` and the
   grid header/scroll contract tests.
3. Run the authenticated Playwright matrix: light/dark theme, normal/fullscreen,
   pinned/unpinned header, portal click, native clipboard, keyboard editing,
   and no-document-scroll.
4. Inspect the rendered shadow DOM for renamed `revogr-*` hosts and update this
   matrix before changing selectors.
5. Capture a cold and warm route trace. A passing unit suite does not prove the
   vendor chunk or scroll lifecycle is unchanged.
