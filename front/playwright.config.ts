import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5000";

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
            // The suites were split into a separate "keycloak" project only
            // because they logged in through SSO; that project was never started
            // by CI, so those regressions (hover flicker, dictionary undo) were
            // silently never checked.
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
    // When running locally: start the app first with `bun dev` or docker
    // E2E_BASE_URL=http://127.0.0.1:5000 bun run test:e2e --project=smoke
});
