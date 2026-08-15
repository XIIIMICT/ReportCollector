import { expect, test } from "@playwright/test";

test("regression: copy, approve dialog, collab bar, column stats", async ({ browser }) => {
    const errors: string[] = [];
    const context = await browser.newContext();
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: "http://127.0.0.1:5000",
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
    });
    await page.goto("http://127.0.0.1:5000/", { waitUntil: "domcontentloaded" });
    await page.waitForURL(
        /127\.0\.0\.1:8080\/realms\/reportcollector\/protocol\/openid-connect\/auth/,
        { timeout: 20000 },
    );
    await page.locator("#username").fill("evtushenko");
    await page.locator("#password").fill("TestPassword123");
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/127\.0\.0\.1:5000/, { timeout: 20000 });
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    // 4. Copy via Ctrl+C: no errors, our copy text (one column per visible grid column).
    const cell = page.locator("revogr-data .rgCell:not(.report-row-header)").first();
    await cell.click();
    await page.waitForTimeout(600);
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(500);
    const copyResult = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as unknown as {
            getColumns: () => Promise<unknown[]>;
        };
        const columnCount = (await grid.getColumns()).length;
        const text = await navigator.clipboard.readText();
        return { columnCount, fieldCount: text ? text.split("\t").length : 0, text };
    });
    expect(copyResult.text.length).toBeGreaterThan(0);
    expect(copyResult.fieldCount).toBeLessThanOrEqual(copyResult.columnCount);

    // Row copy via plain row-header click: whole row, copy event intercepted on window.
    await page.locator(".report-row-header__button").first().click();
    await page.waitForTimeout(600);
    await page.evaluate(() => {
        let bubbleReached = false;
        document.addEventListener("copy", () => {
            bubbleReached = true;
        });
        (window as unknown as { __bubbleReached?: () => boolean }).__bubbleReached = () =>
            bubbleReached;
    });
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(500);
    await page.evaluate(() =>
        (window as unknown as { __bubbleReached?: () => boolean }).__bubbleReached?.(),
    );
    const rowCopy = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as unknown as {
            getColumns: () => Promise<unknown[]>;
        };
        const columnCount = (await grid.getColumns()).length;
        const text = await navigator.clipboard.readText();
        return { columnCount, fieldCount: text ? text.split("\t").length : 0, text };
    });
    expect(rowCopy.fieldCount).toBeGreaterThan(0);
    expect(rowCopy.text).toContain("Алкилат");

    // Ctrl + row-header clicks: only the selected rows are copied, not the range.
    await page.locator("revogr-data .rgCell:not(.report-row-header)").nth(5).click();
    await page.waitForTimeout(500);
    const selAfterCellClick = await page.evaluate(
        () => document.querySelectorAll(".report-row-header--selected").length,
    );
    expect(selAfterCellClick).toBe(0);
    const rowButtons = page.locator(".report-row-header__button");
    await rowButtons.nth(0).click({ modifiers: ["Control"] });
    await page.waitForTimeout(500);
    await rowButtons.nth(2).click({ modifiers: ["Control"] });
    await page.waitForTimeout(600);
    await expect(page.locator("revogr-row-headers .report-row-header--selected")).toHaveCount(2, {
        timeout: 10000,
    });
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(500);
    const ctrlCopy = await page.evaluate(async () => {
        const text = await navigator.clipboard.readText();
        return { lineCount: text ? text.split("\n").length : 0, text };
    });
    expect(ctrlCopy.lineCount).toBe(2);

    // Shift + row-header clicks: a range of rows, not a single row.
    await page.locator("revogr-data .rgCell:not(.report-row-header)").nth(3).click();
    await page.waitForTimeout(500);
    await rowButtons.nth(0).click();
    await rowButtons.nth(5).click({ modifiers: ["Shift"] });
    await page.waitForTimeout(600);
    await expect(page.locator("revogr-row-headers .report-row-header--selected")).toHaveCount(6, {
        timeout: 10000,
    });

    // Paste via Ctrl+V into a single cell: value applied to the focused cell.
    await page.locator("revogr-data .rgCell:not(.report-row-header)").nth(2).click();
    await page.waitForTimeout(500);
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(400);
    const pasteSource = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as unknown as {
            getSelectedRange: () => Promise<{ x: number; y: number }>;
            getSource: () => Promise<Record<string, unknown>[]>;
            getColumns: () => Promise<{ prop: string }[]>;
        };
        const { x, y } = await grid.getSelectedRange();
        const [source, columns] = await Promise.all([grid.getSource(), grid.getColumns()]);
        return { x, y, prop: columns[x]?.prop, value: String(source[y]?.[columns[x]?.prop] ?? "") };
    });
    await page.keyboard.press("ArrowDown");
    await expect
        .poll(
            () =>
                page.evaluate(async () => {
                    const grid = document.querySelector("revo-grid") as unknown as {
                        getSelectedRange: () => Promise<{ y: number }>;
                    };
                    return (await grid.getSelectedRange()).y;
                }),
            { timeout: 10000 },
        )
        .toBe(pasteSource.y + 1);
    await page.keyboard.press("Control+v");
    const pasteResult = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as unknown as {
            getSelectedRange: () => Promise<{ x: number; y: number }>;
            getSource: () => Promise<Record<string, unknown>[]>;
            getColumns: () => Promise<{ prop: string }[]>;
        };
        const { x, y } = await grid.getSelectedRange();
        const [source, columns] = await Promise.all([grid.getSource(), grid.getColumns()]);
        return { x, y, prop: columns[x]?.prop, value: String(source[y]?.[columns[x]?.prop] ?? "") };
    });
    expect(pasteResult.y).toBe(pasteSource.y + 1);
    expect(pasteResult.x).toBe(pasteSource.x);
    await expect
        .poll(
            () =>
                page.evaluate(async () => {
                    const grid = document.querySelector("revo-grid") as unknown as {
                        getSelectedRange: () => Promise<{ x: number; y: number }>;
                        getSource: () => Promise<Record<string, unknown>[]>;
                        getColumns: () => Promise<{ prop: string }[]>;
                    };
                    const { x, y } = await grid.getSelectedRange();
                    const [source, columns] = await Promise.all([
                        grid.getSource(),
                        grid.getColumns(),
                    ]);
                    return String(source[y]?.[columns[x]?.prop] ?? "");
                }),
            { timeout: 15000 },
        )
        .toBe(pasteSource.value);

    // Restore the pasted cell: paste test must not modify real data.
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Control+c");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Control+v");
    await expect
        .poll(
            () =>
                page.evaluate(async () => {
                    const grid = document.querySelector("revo-grid") as unknown as {
                        getSelectedRange: () => Promise<{ x: number; y: number }>;
                        getSource: () => Promise<Record<string, unknown>[]>;
                        getColumns: () => Promise<{ prop: string }[]>;
                    };
                    const { x, y } = await grid.getSelectedRange();
                    const [source, columns] = await Promise.all([
                        grid.getSource(),
                        grid.getColumns(),
                    ]);
                    return String(source[y]?.[columns[x]?.prop] ?? "");
                }),
            { timeout: 15000 },
        )
        .toBe(pasteSource.value);

    // 2. Approve dialog: heading without month, icon left of heading.
    await page.locator(".action-bar button", { hasText: "Зафиксировать" }).click();
    const approveDialog = page.getByRole("alertdialog").filter({
        hasText: "Зафиксировать движение сырья?",
    });
    await expect(approveDialog).toBeVisible({ timeout: 10000 });
    const headerLayout = await page.evaluate(() => {
        const header = document.querySelector(".alert-dialog__header");
        if (!header) return null;
        const style = getComputedStyle(header);
        return { flexDirection: style.flexDirection, alignItems: style.alignItems };
    });
    expect(headerLayout?.flexDirection).toBe("row");
    await approveDialog.getByRole("button", { name: "Отмена" }).click();
    await expect(approveDialog).toHaveCount(0);

    // 3. Collab bar: no "активно" label, no right border (splitter).
    const collabState = await page.evaluate(() => {
        const bar = document.querySelector(".collab-bar");
        if (!bar) return null;
        return {
            text: (bar as HTMLElement).innerText,
            borderRight: getComputedStyle(bar).borderRightWidth,
        };
    });
    expect(collabState?.text).not.toContain("Совместное редактирование активно");
    expect(collabState?.borderRight).toBe("0px");

    // 1. Column stats: the footer shows a compact summary; the full «Столбцы»
    //    breakdown (totaling the visible grid columns) is in the hover tooltip.
    const columnCount = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as unknown as {
            getColumns: () => Promise<unknown[]>;
        };
        return (await grid.getColumns()).length;
    });
    await expect(page.locator(".grid-stats")).toBeVisible({ timeout: 15000 });
    await page.locator(".grid-stats").hover();
    await expect
        .poll(
            () =>
                page.evaluate(() => {
                    const tooltip = document.querySelector(".grid-stats-tooltip");
                    return tooltip?.textContent ?? "";
                }),
            { timeout: 10000 },
        )
        .toContain(`= ${columnCount} всего`);
    expect(errors.length).toBe(0);
    await context.close();
});
