import { defineConfig, devices } from "@playwright/test";

// Local default is the containerized nginx proxy (self-signed TLS); the CI
// stack overrides this with its own loopback origin via E2E_BASE_URL.
const BASE_URL = process.env.E2E_BASE_URL ?? "https://127.0.0.1";

export default defineConfig({
    testDir: "./e2e",
    testIgnore: ["**/probe/**", "**/*-probe*"],
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: 1,
    reporter: [["html", { open: "never" }], ["list"]],
    use: {
        baseURL: BASE_URL,
        // The local proxy generates a self-signed cert; CI runs over plain
        // HTTP and ignores nothing. Never use this to accept a real bad cert.
        ignoreHTTPSErrors: true,
        trace: "on-first-retry",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
        locale: "ru-RU",
    },
    projects: [
        {
            name: "smoke",
            // Every suite signs in through the shared LOCAL login helper, so the
            // whole set runs against one stand — no Keycloak redirect needed.
            // The suites were split into a separate "ui-keycloak" project only
            // because they logged in through SSO; that project is not prepared
            // by CI, so those regressions (hover flicker, dictionary undo) were
            // silently never checked there. Both local and CI runs use THIS
            // config: the `testIgnore` patterns, project names and the smoke
            // selection below are shared. CI's discovery step in ci.yaml checks
            // the same spec basenames — keep both lists in sync.
            testMatch:
                /(?:auth|health|smoke|clipboard|collab|fix-month|hover-dropdown|renovation-hover|raw-materials-ui|workspace-panels)\.spec\.ts/,
            // The timestamp filter sends local midnight as UTC. Pin the zone so
            // the expected instant is the same on a Moscow developer machine
            // and on a UTC CI runner.
            use: { ...devices["Desktop Chrome"], timezoneId: "Europe/Moscow" },
        },
        {
            name: "ui-keycloak",
            testMatch: /workspace-panels\.spec\.ts/,
            use: { ...devices["Desktop Chrome"], timezoneId: "Europe/Moscow" },
        },
    ],
    // When running locally: start the app first with `just dev` (or the
    // isolated CI stack). From the repo root:
    // E2E_BASE_URL=http://127.0.0.1:5000 bun run --cwd front test:e2e --project=smoke
});
