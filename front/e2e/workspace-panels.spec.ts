import { expect, test, type Locator, type Page } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

test.beforeEach(async ({ page }, info) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    if (info.project.name === "ui-keycloak") {
        const username = process.env.E2E_KEYCLOAK_USERNAME;
        const password = process.env.E2E_KEYCLOAK_PASSWORD;
        if (!username || !password)
            throw new Error("Set E2E_KEYCLOAK_USERNAME and E2E_KEYCLOAK_PASSWORD");
        await page.goto("/api/auth/login");
        await page.locator('input[name="username"]').fill(username);
        await page.locator('input[name="password"]').fill(password);
        await page.locator('input[type="submit"], button[type="submit"]').click();
        await expect(page).toHaveURL(/:5000\/(?:renovation|raw-materials|kppk)?$/);
    } else {
        await loginAsLocalUser(page, {
            email: "evtushenko.sn@ruschem.ru",
            password: "TestPassword123",
        });
    }
});

async function openTable(page: Page, url: string) {
    await page.goto(url);
    await expect(page.locator("revo-grid")).toBeVisible({ timeout: 40000 });
    await expect(page.locator("revogr-data .rgCell:not(.report-row-header)").first()).toBeVisible();
}

async function viewAction(page: Page, name: string) {
    await page
        .getByRole("button", { name: "Вид таблицы", exact: true })
        .filter({ visible: true })
        .click();
    await page.getByRole("menuitem", { name, exact: true }).click();
    await page.mouse.move(800, 900);
}

async function height(node: Locator) {
    return node.evaluate((element) => element.getBoundingClientRect().height);
}

async function expectFunnelStable(page: Page, cell: Locator, button: Locator) {
    const box = (await cell.boundingBox())!;
    await page.mouse.move(box.x + box.width - 12, box.y + box.height + 6);
    await page.mouse.move(box.x + box.width - 12, box.y + box.height - 2, { steps: 8 });
    await expect(button).toHaveCSS("opacity", "1");
    const samples = await button.evaluate(async (element) => {
        const values: number[] = [];
        for (let index = 0; index < 30; index++) {
            await new Promise(requestAnimationFrame);
            values.push(Number(getComputedStyle(element).opacity));
        }
        return values;
    });
    expect(Math.min(...samples)).toBe(1);
    await button.click();
    await expect(page.locator(".column-filter__popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".column-filter__popover")).toHaveCount(0);
}

test("toolbar follows visible controls and header funnel stays open at the bottom edge", async ({
    page,
}) => {
    await openTable(page, "/raw-materials");
    const toolbar = page.locator(".workspace-card .table-toolbar");
    const archive = toolbar.locator(".archive-autocomplete");
    await expect(archive).not.toHaveClass(/--hidden/);
    const tall = await height(toolbar);
    await page.getByRole("tab", { name: "Цены", exact: true }).click();
    await expect(archive).toHaveClass(/--hidden/);
    await expect.poll(() => height(toolbar)).toBeLessThan(tall);
    const panel = toolbar.locator("..").locator("..");
    await expect
        .poll(async () => Math.abs((await height(panel)) - (await height(toolbar))))
        .toBeLessThan(1);
    const button = page.locator('.report-header-funnels [data-prop="rawMaterialName"]');
    const cell = page
        .locator(".rgHeaderCell")
        .filter({ has: page.locator('[data-prop="rawMaterialName"]') });
    await expectFunnelStable(page, cell, button);
    await cell.click();
    await expect(cell.locator(".sort-indicator")).toHaveCSS("opacity", "1");
});

test("timestamp filter keeps its bounds and result count after reload, reset persists", async ({
    page,
}) => {
    await openTable(page, "/renovation");
    await viewAction(page, "Показать данные о редактировании строк");
    await page.locator(".revo-grid-external-scrollbar").evaluate((element) => {
        element.scrollLeft = element.scrollWidth;
    });
    const button = page.getByRole("button", { name: "Фильтр: Время добавления", exact: true });
    await button.locator("..").hover({ force: true });
    await button.evaluate((element) => (element as HTMLButtonElement).click());
    const minimum = page.locator(".column-filter__popover .date-time-picker").first();
    await minimum.locator("button").click();
    const time = page.locator(".date-time-picker__popover .date-time-picker__time").first();
    await expect(page.locator(".date-time-picker__popover")).toBeVisible();
    const calendarDay = page.getByRole("button", { name: /среда, 14 октября 2026 г\./ }).last();
    await expect(calendarDay).toBeVisible();
    await calendarDay.click();
    await expect(minimum.locator('[data-type="day"]')).toHaveText("14");
    await expect(time).toBeVisible();
    const beforeRequest = page.waitForResponse(
        (response) =>
            response.url().includes("/plan/rows?") &&
            response.url().includes("createdAt") &&
            response.status() === 200,
    );
    await page.getByRole("button", { name: "Применить", exact: true }).click();
    const before = await beforeRequest;
    const beforeQuery = JSON.parse(new URL(before.url()).searchParams.get("filters")!);
    const beforeBody = await before.json();
    expect(
        beforeQuery.find((filter: { column: string }) => filter.column === "createdAt").from,
    ).toContain("2026-10-13T21:00:00");
    const afterRequest = page
        .waitForResponse(
            (response) =>
                response.url().includes("/plan/rows?") &&
                response.url().includes("createdAt") &&
                response.status() === 200,
        )
        .then((response) => response.url());
    await page.reload();
    const afterUrl = await afterRequest;
    expect(JSON.parse(new URL(afterUrl).searchParams.get("filters")!)).toEqual(beforeQuery);
    await expect(page.getByText(new RegExp(`Строки: ${beforeBody.total} найдено`))).toBeVisible();
    expect(beforeBody.total).toBeGreaterThan(0);
    await viewAction(page, "Сбросить все фильтры и сортировки");
    expect(
        await page.evaluate(() => localStorage.getItem("rc:filters:renovation:plan")),
    ).toBeNull();
});

test("Excel actions share a group and fullscreen Escape closes filter, selection, then fullscreen", async ({
    page,
}) => {
    await openTable(page, "/renovation");
    const download = page.getByRole("button", { name: "Скачать в Excel", exact: true });
    const group = download.locator("..");
    await expect(group).toHaveAttribute("data-slot", "button-group");
    await expect(
        group.getByRole("button", { name: "Загрузить из Excel", exact: true }),
    ).toBeVisible();
    await download.click();
    await expect(page.getByRole("menuitem", { name: "Скачать видимую таблицу" })).toBeVisible();
    await page.keyboard.press("Escape");
    await viewAction(page, "В полный экран");
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    const cell = page
        .locator(
            "revogr-data .rgCell:not(.report-row-header):not(.renovation-grid__summary-cell):not(.disabled):visible",
        )
        .first();
    await cell.click({ force: true });
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(1);
    const filter = page
        .locator(".renovation-grid__header-cell")
        .filter({ has: page.locator(".header-filter-button") })
        .first();
    await filter.hover();
    await filter.locator("button").click();
    await expect(page.locator(".column-filter__popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".column-filter__popover")).toHaveCount(0);
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(0);
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    await page.keyboard.press("Escape");
    await expect(page.locator(".app-shell")).not.toHaveClass(/app-shell--fullscreen/);
});
