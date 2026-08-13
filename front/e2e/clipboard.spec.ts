import { expect, test } from "@playwright/test";

async function loginAsEvtushenko(page: import("@playwright/test").Page) {
    await page.goto("http://127.0.0.1:5000/", { waitUntil: "domcontentloaded" });
    await page.waitForURL(
        /127\.0\.0\.1:8080\/realms\/reportcollector\/protocol\/openid-connect\/auth/,
        { timeout: 20000 },
    );
    await page.locator("#username").fill("evtushenko");
    await page.locator("#password").fill("TestPassword123");
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/127\.0\.0\.1:5000/, { timeout: 20000 });
}

test("clipboard: keyboard copy and paste applies values to cells", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: "http://127.0.0.1:5000",
    });
    await loginAsEvtushenko(page);
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    await page.getByRole("tab", { name: "Цены" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    // Цены: столбцы — сырьё (0), актив (1), базис (2), факт (3), бизнес-план (4-7).
    // Внутри revo-grid несколько revogr-data (пустые оболочки + заголовки строк с 1 ячейкой);
    // строки данных отличаются наличием 8 ячеек.
    const dataRows = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(8)") });
    const rowCells = dataRows.first().locator(".rgCell");
    const source = rowCells.nth(4); // Объём, т — редактируемое число
    const target = rowCells.nth(5); // USD / т — редактируемое число
    await expect(source).toBeVisible();

    // Заполняем исходную ячейку через редактор.
    await source.dblclick();
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    await editor.fill("42");
    await editor.press("Enter");
    await expect(source).toContainText("42", { timeout: 15000 });

    // Копирование с клавиатуры: Ctrl+C кладёт значение в буфер.
    await source.click();
    await page.keyboard.press("Control+C");
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboard).toContain("42");

    // Вставка с клавиатуры: Ctrl+V применяет значение в выбранную ячейку.
    await target.click();
    await page.keyboard.press("Control+V");
    await expect(target).toContainText("42", { timeout: 15000 });
});

test("clipboard: toolbar copy/paste buttons apply values", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: "http://127.0.0.1:5000",
    });
    await loginAsEvtushenko(page);
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    await page.getByRole("tab", { name: "Цены" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    const dataRows = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(8)") });
    const rowCells = dataRows.first().locator(".rgCell");
    const source = rowCells.nth(4);
    const target = rowCells.nth(6); // руб. / т
    await expect(source).toBeVisible();

    await source.dblclick();
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    await editor.fill("77");
    await editor.press("Enter");
    await expect(source).toContainText("77", { timeout: 15000 });

    await source.click();
    await page.getByRole("button", { name: "Копировать" }).last().click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("77");

    await target.click();
    await page.getByRole("button", { name: "Вставить" }).last().click();
    await expect(target).toContainText("77", { timeout: 15000 });
});
