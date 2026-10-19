/**
 * Stylelint for the project's hand-written CSS.
 *
 * Only `front/src/app/globals.css` (and any future plain CSS the app owns) is
 * linted: everything else is generated (`.next/`, `node_modules/`). Tailwind
 * runs through PostCSS, so the file uses `@theme`, `@apply` and friends —
 * the config allows Tailwind at-rules and does not try to validate unknown
 * `tailwindcss` syntax.
 *
 * The rule set is deliberately correctness-first, not opinionated about
 * formatting: `oxfmt` owns the layout (it formats CSS too), and the repo's
 * styling convention is semantic utility classes plus theme variables, so
 * stylelint checks for real mistakes — invalid/duplicate declarations,
 * unknown or misspelled properties, broken selectors, and longhand pairs that
 * would silently collapse.
 *
 * `.mjs`, not `.ts` (unlike `next.config.ts`/`postcss.config.ts`): stylelint
 * reads a TypeScript config through cosmiconfig, which calls
 * `typescript.findConfigFile` and `typescript.transpileModule`. This project
 * runs **TypeScript 7** — the native port, whose JS entry point exposes only
 * `{ version }` — so both calls are missing and stylelint dies with
 * `typescript.findConfigFile is not a function`. Keeping `.ts` would require a
 * shim that fakes four TypeScript APIs before every stylelint run; a plain ES
 * module config needs nothing.
 */
export default {
    ignoreFiles: [
        "**/.next/**",
        "**/node_modules/**",
        "**/test-results/**",
        "**/playwright-report/**",
        "**/coverage/**",
    ],
    rules: {
        // ── Correctness ────────────────────────────────────────────────────
        "block-no-empty": true,
        "declaration-block-no-duplicate-properties": [
            true,
            { ignore: ["consecutive-duplicates-with-different-syntaxes"] },
        ],
        "declaration-block-no-shorthand-property-overrides": true,
        "no-duplicate-selectors": true,
        "no-invalid-double-slash-comments": true,
        "no-invalid-position-at-import-rule": true,
        "property-no-unknown": [true, { ignoreProperties: ["overflow-anchor"] }],
        "selector-pseudo-class-no-unknown": [true, { ignorePseudoClasses: ["global", "local"] }],
        "selector-pseudo-element-no-unknown": true,
        "selector-type-no-unknown": [true, { ignore: ["custom-elements"] }],
        "unit-no-unknown": true,
        "function-no-unknown": [true, { ignoreFunctions: ["theme", "--alpha", "oklch"] }],
        "at-rule-no-unknown": [
            true,
            {
                ignoreAtRules: [
                    "apply",
                    "config",
                    "custom-variant",
                    "layer",
                    "plugin",
                    "reference",
                    "source",
                    "tailwind",
                    "theme",
                    "utility",
                    "variant",
                ],
            },
        ],

        // ── Consistency with the design system ─────────────────────────────
        "color-no-invalid-hex": true,
        "function-url-quotes": "always",
        "shorthand-property-no-redundant-values": true,
        // Longhands stay where they are intentional: `overflow-x: auto` +
        // `overflow-y: hidden` is NOT `overflow`, and `inset: 0` would clash
        // with the four sides spelled out next to it.
        "declaration-block-no-redundant-longhand-properties": [
            true,
            { ignoreShorthands: ["overflow", "inset"] },
        ],
        "media-feature-name-no-unknown": true,

        // ── Specificity hygiene ────────────────────────────────────────────
        // `!important` is legitimate here (react-aria inlines styles we must
        // override), so it is not banned; only nonsense selectors are caught.
        "no-descending-specificity": null,
        "selector-anb-no-unmatchable": true,
    },
};
