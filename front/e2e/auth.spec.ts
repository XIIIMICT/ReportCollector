import { expect, test } from "@playwright/test";

import { loginAsLocalUser, logout } from "./helpers/auth";

const LOCAL_EMAIL = "evtushenko.sn@ruschem.ru";
const LOCAL_PASSWORD = "TestPassword123";

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
