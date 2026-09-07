import type { Page } from "@playwright/test";

/**
 * Origin, на который указывает AUTH_APP_ORIGIN: после локального входа
 * 303-редирект ведёт именно туда, поэтому и форму открываем на нём.
 */
const LOCAL_APP_ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5000";

/**
 * Log in via the local password login page
 * (KEYCLOAK_ENABLED=false).
 */
export async function loginAsLocalUser(page: Page, options: { email: string; password: string }) {
    const { email, password } = options;

    await page.goto(`${LOCAL_APP_ORIGIN}/api/auth/login`);
    await page.locator('input[name="email"]').fill(email);
    await page.locator('input[name="password"]').fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(
        (url) => url.origin === LOCAL_APP_ORIGIN && !url.pathname.startsWith("/api/auth/"),
    );
}

/**
 * Clear session cookie to log out.
 */
export async function logout(page: Page) {
    await page.context().clearCookies();
}
