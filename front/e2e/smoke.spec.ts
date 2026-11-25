import { expect, test } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

test("renovation single-cell edit persists and Ctrl+A stays disabled", async ({ page }) => {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await page.goto("/renovation");
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    // The first chunk arrives asynchronously: take the row once it is loaded.
    // The plan OPENS AT ITS TAIL (rows are appended at the end), so this is not
    // «№» 1 — the real position is kept alongside the id.
    await expect
        .poll(
            () =>
                page.evaluate(async () => {
                    const grid = document.querySelector("revo-grid");
                    if (!grid) return 0;
                    const rows = await grid.getVisibleSource("rgRow");
                    return rows.filter((row) => row && row.id).length;
                }),
            { timeout: 20000 },
        )
        .toBeGreaterThan(0);
    const target = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const rows = await grid.getVisibleSource("rgRow");
        const row = rows.find((candidate) => candidate && candidate.id);
        return { rowId: String(row.id), position: Number(row.__position ?? 1) };
    });
    const rowId = target.rowId;
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const columnIndex = columns.findIndex((column) => column.prop === "process_position");
        if (columnIndex < 0) throw new Error("Missing process_position");
        const rows = await grid.getVisibleSource("rgRow");
        const rowIndex = rows.findIndex((row) => row && row.id);
        // `setCellEdit` opens the editor directly — on revo-grid 4.31 the old
        // `setCellsFocus(...)` + `Enter` left focus on the body and never mounted
        // `revogr-edit`. Both calls share ONE evaluate: scrolling re-renders the
        // grid and would drop the editor's focus.
        await grid.scrollToColumnIndex(columnIndex);
        await grid.setCellEdit(rowIndex, "process_position");
    });
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    // The editor mounts before it accepts input: type only once it holds focus,
    // or `fill` lands nowhere and Enter commits the unchanged value.
    await expect(editor).toBeFocused();
    // A fresh value per run: the grid skips a commit when the text equals what
    // is already stored.
    const value = `CI renovation ${Date.now()}`;
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/renovation/plan?") &&
            response.request().method() === "PATCH",
        { timeout: 15000 },
    );
    await editor.fill(value);
    await expect(editor).toHaveValue(value);
    await editor.press("Enter");
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    // The grid re-opens at its remembered (or tail) position: jump to the row's
    // «№» first, then read it back by id.
    await page.evaluate(async (position) => {
        const grid = document.querySelector("revo-grid")!;
        await grid.scrollToRow(position - 1);
    }, target.position);
    await expect
        .poll(
            () =>
                page.evaluate(async (id) => {
                    const grid = document.querySelector("revo-grid");
                    if (!grid) return null;
                    const row = (await grid.getVisibleSource("rgRow")).find(
                        (candidate) => candidate.id === id,
                    );
                    return row ? (row.process_position ?? "") : null;
                }, rowId),
            { timeout: 25000 },
        )
        .toBe(value);
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const index = columns.findIndex((column) => column.prop === "process_position");
        await grid.setCellsFocus({ x: index, y: 0 }, { x: index, y: 0 });
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
    // The first two price columns are pinned into a separate viewport; the
    // editable business-plan values are in the scrollable `rgCol` section.
    const cell = page
        .locator("revogr-viewport-scroll.rgCol revogr-data .rgRow")
        .first()
        .locator(".rgCell")
        .nth(2); // Объём, т
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
    // The status spells a single-cell selection as «столбец N, строка M» — the
    // column NUMBER of the form, 1-based («Объём, т» is the 5th). It used to show
    // the column label; the shared selection label now prefers the number, so
    // the assertion follows the current contract.
    await expect(page.locator(".form-status__selection")).toContainText("столбец 5");
    // Open the editor programmatically: a `dblclick` races with the grid's own
    // selection handling (and the focus moved by the click above).
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        await grid.setCellEdit(0, "businessPlanQuantityTons");
    });
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    await expect(editor).toBeFocused();
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "PATCH",
        { timeout: 15000 },
    );
    await editor.fill(value);
    await expect(editor).toHaveValue(value);
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
