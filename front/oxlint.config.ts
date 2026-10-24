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
    plugins: [
        "react",
        "react-perf",
        "nextjs",
        "vitest",
        "promise",
        "node",
        "import",
        "jsdoc",
    ],
    categories: {
        correctness: "error",
        suspicious: "warn",
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
            },
        },
    ],
});
