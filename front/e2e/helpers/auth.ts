import type { Page } from "@playwright/test";

/**
 * Origin, на который указывает AUTH_APP_ORIGIN: после локального входа
 * 303-редирект ведёт именно туда, поэтому и форму открываем на нём.
 */
const LOCAL_APP_ORIGIN = process.env.E2E_BASE_URL ?? "https://127.0.0.1";

/**
 * Log in via the local password login page
 * (KEYCLOAK_ENABLED=false).
 */
export async function loginAsLocalUser(page: Page, options: { email: string; password: string }) {
    const { email, password } = options;
    if (!email || !password) {
        throw new Error(
            "loginAsLocalUser needs both email and password (see helpers/test-users.ts).",
        );
    }

    await page.goto(`${LOCAL_APP_ORIGIN}/api/auth/login`);
    // Assert the login round-trip itself. The redirect target comes from the
    // server's AUTH_APP_ORIGIN, which may differ from E2E_BASE_URL; deriving
    // the expected origin from the response avoids that configuration trap.
    const responsePromise = page.waitForResponse(
        (response) =>
            response.url().includes("/api/auth/login") && response.request().method() === "POST",
        { timeout: 10000 },
    );
    await page.locator('input[name="email"]').fill(email);
    await page.locator('input[name="password"]').fill(password);
    await page.locator('button[type="submit"]').click();
    const response = await responsePromise;
    if (response.status() !== 303) {
        throw new Error(`Local login did not redirect (HTTP ${response.status()}).`);
    }
    const location = response.headers()["location"];
    if (!location) {
        throw new Error("Local login redirect is missing its Location header.");
    }
    const expectedOrigin = new URL(location, page.url()).origin;
    // The redirected workspace keeps streaming requests open (grid data,
    // collaboration), so DOMContentLoaded is the right boundary. Keep the
    // wait below the default 30 s test timeout instead of asking for 60 s.
    await page.waitForURL(
        (url) => url.origin === expectedOrigin && !url.pathname.startsWith("/api/auth/"),
        { waitUntil: "domcontentloaded", timeout: 10000 },
    );
}

/**
 * Clear session cookie to log out.
 */
export async function logout(page: Page) {
    await page.context().clearCookies();
}
