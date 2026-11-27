/**
 * CI detection shared by e2e preconditions.
 *
 * `playwright.config.ts` already keys retries and `forbidOnly` off `CI`, which
 * is what GitHub Actions sets to "true". Other runners use "1"; an explicitly
 * set "false"/"0" (or an empty value) means a developer machine. Treat any
 * other non-empty value as CI so a precondition can never silently skip in an
 * automated run.
 */
export function isCI(): boolean {
    const value = process.env.CI;
    if (value === undefined) return false;
    const normalized = value.trim().toLowerCase();
    return normalized !== "" && normalized !== "0" && normalized !== "false";
}
