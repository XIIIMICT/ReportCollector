import { expect, test } from "@playwright/test";

import { loginAsLocalUser, logout } from "./helpers/auth";

const LOCAL_EMAIL = "evtushenko.sn@ruschem.ru";
const LOCAL_PASSWORD = "TestPassword123";

test.beforeEach(async ({ page }) => {
    test.skip(
        !(await localAuthEnabled(page)),
        "локальный вход выключен (Keycloak включён) — тесты локальной авторизации пропущены",
    );
});

async function localAuthEnabled(page: import("@playwright/test").Page): Promise<boolean> {
    await page.goto("/api/auth/login", { waitUntil: "domcontentloaded" });
    const input = page.locator('input[name="email"]');
    try {
        await input.waitFor({ state: "visible", timeout: 5000 });
        return true;
    } catch {
        return false;
    }
}

test.describe("Авторизация", () => {
    test("локальный вход и выход", async ({ page }) => {
        await loginAsLocalUser(page, { email: LOCAL_EMAIL, password: LOCAL_PASSWORD });
        await expect(page).toHaveURL(/\/$/);
        // Логаут через кнопку профиля
        await page.goto("/api/auth/logout");
        await expect(page).toHaveURL(/\/api\/auth\/login/);
    });

    test("неверный пароль показывает ошибку", async ({ page }) => {
        await logout(page);
        await page.goto("/api/auth/login");
        await page.locator('input[name="email"]').fill(LOCAL_EMAIL);
        await page.locator('input[name="password"]').fill("WrongPassword1");
        await page.locator('button[type="submit"]').click();
        await expect(page.locator("p.error")).toContainText("Неверный логин или пароль.");
    });

    test("редирект неавторизованного пользователя на login", async ({ page }) => {
        await logout(page);
        await page.goto("/");
        await expect(page).toHaveURL(/\/api\/auth\/login/);
    });
});
