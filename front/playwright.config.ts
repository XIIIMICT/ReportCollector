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
            testMatch: /(?:auth|health|smoke|clipboard|collab)\.spec\.ts/,
            use: { ...devices["Desktop Chrome"] },
        },
        {
            name: "keycloak",
            testMatch: [
                "hover-dropdown.spec.ts",
                "raw-materials-ui.spec.ts",
                "renovation-hover.spec.ts",
            ],
            use: { ...devices["Desktop Chrome"] },
        },
    ],
    // When running locally: start the app first with `bun dev` or docker
    // E2E_BASE_URL=http://127.0.0.1:5000 bun run test:e2e --project=smoke
});
