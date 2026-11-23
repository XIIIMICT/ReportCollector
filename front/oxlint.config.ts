import { defineConfig } from "oxlint";

/**
 * oxlint — the JS/TS half of `bun lint` (stylelint handles CSS).
 *
 * The defaults alone are close to useless for this codebase: oxlint ships with
 * ONLY `unicorn`, `typescript` and `oxc` enabled, so React, imports and the
 * test helpers went unchecked — and the `eslint-disable-next-line
 * react-hooks/exhaustive-deps` comments scattered around were silently dead,
 * because that plugin was never loaded.
 *
 * Plugins: `react` (hooks and JSX), `react_perf` (unstable render inputs),
 * `nextjs` (App Router conventions), `vitest` (the runner behind `bun test` —
 * NOT `jest`, which would flag every `bun:test` import), `promise`, `node`,
 * `import` and `jsdoc`. `typescript`/`unicorn`/`oxc` stay on from the
 * defaults, so they are not repeated here. Rule names are oxlint's own
 * (e.g. `react/exhaustive-deps`, not `react-hooks/exhaustive-deps`).
 *
 * `categories` enables the recommended sets per plugin; `rules` below carries
 * the deliberate deviations, each with its reason inline.
 */
export default defineConfig({
    plugins: ["react", "react-perf", "nextjs", "vitest", "promise", "node", "import", "jsdoc"],
    categories: {
        correctness: "error",
        suspicious: "warn",
        // `react-perf` lives here, deliberately as a HINT, not a debt. The rule
        // asks for memoized JSX props, which only pay off under `React.memo` —
        // and this codebase has no `memo()` at all: it relies on React Compiler
        // (`reactCompiler: true` in next.config.ts), which memoizes JSX props,
        // object literals and arrow functions by itself. Most of what is flagged
        // is attributes on DOM nodes (style/onPointerDown on a <div>), which have
        // no prop comparison to lose. If the compiler is ever turned off, or a
        // component starts using `memo()`, these findings become real work.
        perf: "warn",
    },
    env: {
        browser: true,
        es2024: true,
        node: true,
    },
    rules: {
        // React — React 19 with the automatic JSX runtime.
        "react/react-in-jsx-scope": "off",
        "react/jsx-max-depth": "off",
        "react/jsx-props-no-spreading": "off", // spread props are idiomatic here
        // Hooks: correctness stays on; the dependency list is a warning, since
        // several effects intentionally run once on mount.
        "react/rules-of-hooks": "error",
        // Every hook in the codebase is checked; the handful of callbacks that
        // depend on FIELDS of inline-built objects (rather than the objects) are
        // documented at the call site.
        "react/exhaustive-deps": "warn",
        // The codebase composes components freely (section + row helpers).
        "react/no-multi-comp": "off",
        // `renderAction`/`renderItem` style render-props are passed as JSX props
        // by design (HoverDropdown `trigger`, table cells). The rule cannot tell
        // a render-prop from a component defined during render.
        "react/no-unstable-nested-components": ["warn", { allowAsProps: true }],
        // Refs are written from effects, handlers and ref callbacks — the rule
        // reports those as "modification after render", which is the React idiom.
        "react/immutability": "off",
        // `someRef.current = value` in the render body is the OFFICIAL React
        // pattern for "always call the latest callback" (`useLatest`): it keeps a
        // document listener or a memoized handler identity-stable while still
        // seeing fresh props. The rule flags every such write, plus the `ref`
        // reads inside handlers, so on this codebase it is a false positive.
        "react/refs": "off",
        // `setState` inside an effect is how a form reset runs when the dialog
        // opens (`if (isOpen) setTarget(null)`), how a section switch drops its
        // placeholder row, and how any external store (matchMedia, WebSocket
        // status) is mirrored into React state. The rule bans the whole idiom.
        "react/set-state-in-effect": "off",
        // Fires on `useMemo` bodies that switch over props and on the compiler's
        // own memoization hints; the codebase memoizes by hand already.
        "react/preserve-manual-memoization": "off",

        // The plan grid's synthetic row fields use the `__` prefix on purpose:
        // `RenovationGridRow` is `& Record<string, unknown>`, so the prefix is
        // what keeps these internal fields from ever colliding with a database
        // column (`col_002`…`col_218`) — the same convention gives `__id`,
        // `__position`, `__rowState`. The names are part of the row contract
        // between the grid, the workspace and the export, so they are allow-listed
        // rather than renamed.
        "no-underscore-dangle": [
            "warn",
            {
                allow: [
                    "__id",
                    "__index",
                    "__position",
                    "__assetId",
                    "__assetName",
                    "__renovationId",
                    "__rowState",
                    "__changedFields",
                    "__missingComponents",
                    "__invalidFields",
                ],
            },
        ],

        // Tests: `bun test` imports its helpers from `bun:test` rather than
        // using vitest globals, which those rules assume.
        "vitest/require-hook": "off",
        "vitest/prefer-importing-vitest-globals": "off",

        // Imports: the "@/" alias is resolved through tsconfig; only real
        // problems are reported, not ordering or dependency budgets.
        "import/no-unassigned-import": "off", // CSS/style side-effect imports
        "import/max-dependencies": "off",

        // jsdoc: the codebase documents with plain comment blocks, not tags.
        "jsdoc/require-param": "off",
        "jsdoc/require-returns": "off",

        // Node: the front end never touches the filesystem.
        "node/no-process-env": "off",

        // The two places that key a list by index render a FIXED list of strings
        // that never reorder, insert or delete (the import modal's rejected-value
        // list, and the tooltip lines of one header chain) — the index is stable
        // for the lifetime of the node, which is exactly when the rule does not
        // apply. Any list that can change must still use a real key.
        "react/no-array-index-key": "off",

        // The <a> tags left in the app point at an API route (`/api/auth/logout`,
        // which must NOT be a client-side navigation) and at the app root from an
        // error card — neither is a page transition <Link> can optimise.
        "nextjs/no-html-link-for-pages": "off",
        // The only <img> is the fixed-size brand icon in the header. It is a few
        // kB, painted before hydration, and hides itself via `onError` — the
        // optimizer would add a resizing contract for no measurable gain.
        "nextjs/no-img-element": "off",
        // A `then` that only performs an effect returns nothing on purpose; the
        // rule targets promise chains whose value is consumed downstream, which
        // none of these are.
        "promise/always-return": "off",
    },
    overrides: [
        {
            // Tests and e2e specs may use dev-only patterns.
            files: ["**/*.test.ts", "**/*.test.tsx", "**/__tests__/**", "e2e/**"],
            rules: {
                "no-console": "off",
                // A test harness assigns to a variable captured from the test
                // body to drive the hook from outside React — the point of the
                // trick, not module-level state in a component.
                "react/globals": "off",
            },
        },
    ],
});
