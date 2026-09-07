import { expect, test } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

test("renovation single-cell edit persists and Ctrl+A stays disabled", async ({ page }) => {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await page.goto("/renovation");
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    const rowId = await page.evaluate(async () =>
        String((await document.querySelector("revo-grid")!.getVisibleSource("rgRow"))[0].id),
    );
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const index = columns.findIndex((column) => column.prop === "col_011");
        if (index < 0) throw new Error("Missing col_011");
        await grid.scrollToColumnIndex(index);
        await grid.setCellsFocus({ x: index, y: 0 }, { x: index, y: 0 });
        await grid.refresh();
    });
    await page.keyboard.press("Enter");
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    const value =
        (await editor.inputValue()) === "CI renovation object"
            ? "CI updated object"
            : "CI renovation object";
    await editor.fill(value);
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/renovation/plan?") &&
            response.request().method() === "PATCH",
    );
    await editor.press("Enter");
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await expect
        .poll(() =>
            page.evaluate(async (id) => {
                const grid = document.querySelector("revo-grid");
                if (!grid) return null;
                return (await grid.getVisibleSource("rgRow")).find((row) => row.id === id)?.col_011;
            }, rowId),
        )
        .toBe(value);
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        await grid.setCellsFocus({ x: 1, y: 0 }, { x: 1, y: 0 });
        await grid.refresh();
    });
    const readRange = () =>
        page.evaluate(() => document.querySelector("revo-grid")!.getSelectedRange());
    const before = await readRange();
    expect(before).not.toBeNull();
    await page.keyboard.press("Control+A");
    await expect.poll(readRange).toEqual(before);
});

test("production forms load and a price edit survives reload", async ({ page }) => {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await page.goto("/renovation");
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    await page.goto("/raw-materials");
    await expect(page.getByRole("tab", { name: "Цены" })).toBeVisible({ timeout: 30000 });
    const pricesLoaded = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "GET",
    );
    await page.getByRole("tab", { name: "Цены" }).click();
    expect((await pricesLoaded).ok()).toBe(true);
    const rows = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(8)") });
    const cell = rows.first().locator(".rgCell").nth(4);
    await expect(cell).toBeVisible();
    const rowId = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const rows = await grid.getVisibleSource("rgRow");
        return String(rows[0].id);
    });
    const response = await page.request.get("/api/raw-materials/prices");
    expect(response.ok()).toBe(true);
    const prices = (await response.json()) as {
        id: string;
        businessPlanQuantityTons: number | null;
    }[];
    const original = String(prices.find((row) => row.id === rowId)!.businessPlanQuantityTons ?? "");
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(original);
    const value = original === "4242" ? "4343" : "4242";
    await expect(page.locator(".form-status__dot--online")).toBeVisible();
    // Move DOM focus off the tab button before sending the grid's Enter.
    await cell.click();
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const index = columns.findIndex((column) => column.prop === "businessPlanQuantityTons");
        if (index < 0) throw new Error("Missing businessPlanQuantityTons");
        await grid.setCellsFocus({ x: index, y: 0 }, { x: index, y: 0 });
        await grid.refresh();
    });
    await expect(page.locator(".form-status__selection")).toContainText("Объём");
    await cell.dblclick({ delay: 100 });
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    await editor.fill(value);
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "PATCH",
    );
    await editor.press("Enter");
    expect((await saved).ok()).toBe(true);
    const undone = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/undo?") &&
            response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Отменить действие", exact: true }).click();
    expect((await undone).ok()).toBe(true);
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(original);
    const redone = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/redo?") &&
            response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Вернуть действие", exact: true }).click();
    expect((await redone).ok()).toBe(true);
    await page.reload();
    await page.getByRole("tab", { name: "Цены" }).click();
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(value);
});
