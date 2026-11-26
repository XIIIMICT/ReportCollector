import { expect, test } from "@playwright/test";

import { loginAsLocalUser, logout } from "./helpers/auth";
import { TEST_USERS, testUserCredentials } from "./helpers/test-users";

test.describe("Авторизация", () => {
    test("локальный вход и выход", async ({ page }) => {
        await loginAsLocalUser(page, testUserCredentials("admin"));
        await expect(page).toHaveURL(/\/(?:renovation|raw-materials|kppk)?$/);
        // Логаут через кнопку профиля
        await page.goto("/api/auth/logout");
        await expect(page).toHaveURL(/\/api\/auth\/login/);
    });

    test("неверный пароль показывает ошибку", async ({ page }) => {
        await logout(page);
        await page.goto("/api/auth/login");
        await page.locator('input[name="email"]').fill(TEST_USERS.admin.email);
        await page.locator('input[name="password"]').fill("WrongPassword1");
        await page.locator('button[type="submit"]').click();
        await expect(page.locator("p.error")).toContainText("Неверный email или пароль.");
    });

    test("редирект неавторизованного пользователя на login", async ({ page }) => {
        await logout(page);
        await page.goto("/");
        await expect(page).toHaveURL(/\/api\/auth\/login/);
    });
});
