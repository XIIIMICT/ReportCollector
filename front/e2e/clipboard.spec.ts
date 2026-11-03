import { expect, test } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

const APP_ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5000";

test("sorted Excel paste updates the visible row and preserves its neighbor", async ({
    page,
    context,
}) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: APP_ORIGIN });
    await loginAsEvtushenko(page);
    const loaded = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "GET",
    );
    await page.getByRole("tab", { name: "Цены" }).click();
    expect((await loaded).ok()).toBe(true);
    const originalFirst = await page.evaluate(async () =>
        String((await document.querySelector("revo-grid")!.getVisibleSource("rgRow"))[0].id),
    );
    // Sort the way a user does. `grid.updateColumnSorting()` only emits a
    // sorting request; the workspace owns the applied sorting and pushes it to
    // the grid itself, so the direct call changes nothing on screen.
    const materialHeader = page
        .locator("revo-grid .rgHeaderCell")
        .filter({ has: page.locator('[data-prop="rawMaterialName"]') });
    await materialHeader.click();
    await materialHeader.click();
    await expect
        .poll(() =>
            page.evaluate(async () =>
                String(
                    (await document.querySelector("revo-grid")!.getVisibleSource("rgRow"))[0].id,
                ),
            ),
        )
        .not.toBe(originalFirst);
    const ids = await page.evaluate(async () =>
        (await document.querySelector("revo-grid")!.getVisibleSource("rgRow"))
            .slice(0, 2)
            .map((row) => String(row.id)),
    );
    const beforeResponse = await page.request.get("/api/raw-materials/prices");
    expect(beforeResponse.ok()).toBe(true);
    const before = (await beforeResponse.json()) as {
        id: string;
        businessPlanQuantityTons: number | null;
    }[];
    const value =
        before.find((row) => row.id === ids[0])!.businessPlanQuantityTons === 6543 ? 6544 : 6543;
    const cell = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(8)") })
        .first()
        .locator(".rgCell")
        .nth(4);
    await cell.click();
    await page.evaluate((text) => navigator.clipboard.writeText(text), `${value}\r\n`);
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "PATCH",
    );
    await page.keyboard.press("Control+V");
    const response = await saved;
    expect(response.ok()).toBe(true);
    const body = response.request().postDataJSON() as { updates: { rowId: string }[] };
    expect(body.updates.map((update) => update.rowId)).toEqual([ids[0]]);
    const after = (await (
        await page.request.get("/api/raw-materials/prices")
    ).json()) as typeof before;
    expect(after.find((row) => row.id === ids[0])!.businessPlanQuantityTons).toBe(value);
    expect(after.find((row) => row.id === ids[1])!.businessPlanQuantityTons).toBe(
        before.find((row) => row.id === ids[1])!.businessPlanQuantityTons,
    );
});

async function loginAsEvtushenko(page: import("@playwright/test").Page) {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await page.goto("/raw-materials");
    await expect(page.getByRole("tab", { name: "Цены" })).toBeVisible({ timeout: 20000 });
}

test("clipboard: keyboard copy and paste applies values to cells", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: APP_ORIGIN,
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

test("clipboard: Ctrl+C works with Russian keyboard layout", async ({ page, context }) => {
    // В русской раскладке event.key — «с»/«м», поэтому обработчики должны
    // опираться на event.code (физическую клавишу), а не на event.key.
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: APP_ORIGIN,
    });
    await loginAsEvtushenko(page);
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    const firstCell = page.locator("revogr-data .rgCell:not(.report-row-header)").first();
    await firstCell.click();
    await page.waitForTimeout(300);
    const expected = (await firstCell.innerText()).trim();
    expect(expected).not.toBe("");
    await page.evaluate(() => navigator.clipboard.writeText(""));

    // Синтетическое нажатие Ctrl+C «в русской раскладке» (key = «с», code = KeyC).
    await page.evaluate(() => {
        const cell = document.activeElement as HTMLElement | null;
        cell?.dispatchEvent(
            new KeyboardEvent("keydown", {
                key: "с",
                code: "KeyC",
                ctrlKey: true,
                bubbles: true,
                composed: true,
            }),
        );
    });

    await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()), { timeout: 5000 })
        .toContain(expected);
});

test("clipboard: reference fields paste validates values and rejects unknown", async ({
    page,
    context,
}) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: APP_ORIGIN,
    });
    await loginAsEvtushenko(page);
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    await page.getByRole("tab", { name: "Сырьевые позиции" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    const dataRows = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(2)") });
    const firstCell = dataRows.first().locator(".rgCell").nth(1);
    await expect(firstCell).toBeVisible();
    await firstCell.click();

    const readAssets = () =>
        page.evaluate((rowIndex) => {
            const grid = document.querySelector("revo-grid");
            // null during a grid remount: an empty read lets callers retry.
            if (!grid) return "null";
            return Promise.all([grid.getSource(), grid.getColumns()]).then(([source, cols]) => {
                const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
                const col = flat.find((c) => c.prop === "assetNames");
                return JSON.stringify(source[rowIndex]?.[col?.prop]);
            });
        }, 0);

    // Невалидная вставка: один из активов отсутствует в справочнике → вся
    // вставка отклоняется, значение не меняется, показывается предупреждение.
    const before = await readAssets();
    await page.evaluate(() => navigator.clipboard.writeText("СНХЗ, НЕСУЩЕСТВУЮЩИЙ АКТИВ"));
    await page.keyboard.press("Control+V");
    await expect(
        page.getByText("значение «НЕСУЩЕСТВУЮЩИЙ АКТИВ» отсутствует в справочнике."),
    ).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(800);
    expect(await readAssets()).toBe(before);

    // Валидная вставка применяется: найдём строку, где набор активов — подмножество
    // «ВОС, СНХЗ» (иначе сервер отклонит снятие активов с движением).
    const target = await page.evaluate(() => {
        const grid = document.querySelector("revo-grid")!;
        return Promise.all([grid.getSource(), grid.getColumns()]).then(([source, cols]) => {
            const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
            const col = flat.find((c) => c.prop === "assetNames");
            return source.findIndex((row) => {
                const set = new Set<string>(row[col?.prop] ?? []);
                return set.size <= 2 && [...set].every((a) => a === "ВОС" || a === "СНХЗ");
            });
        });
    });
    expect(target, "Seed must contain a compatible material").toBeGreaterThanOrEqual(0);
    {
        const cell = dataRows.nth(target).locator(".rgCell").nth(1);
        await expect(cell).toBeVisible();
        await cell.click();
        await page.evaluate(() => navigator.clipboard.writeText("ВОС, СНХЗ"));
        await page.keyboard.press("Control+V");
        await expect
            .poll(
                () =>
                    page.evaluate((rowIndex) => {
                        const grid = document.querySelector("revo-grid");
                        // null during a grid remount: return "" so the poll
                        // retries instead of failing.
                        if (!grid) return "";
                        return Promise.all([grid.getSource(), grid.getColumns()]).then(
                            ([source, cols]) => {
                                const flat = cols.flatMap((c) =>
                                    c.children?.length ? c.children : [c],
                                );
                                const col = flat.find((c) => c.prop === "assetNames");
                                return JSON.stringify(source[rowIndex]?.[col?.prop]);
                            },
                        );
                    }, target),
                { timeout: 15000 },
            )
            .toBe(JSON.stringify(["ВОС", "СНХЗ"]));
    }
});

test("clipboard: toolbar copy/paste buttons apply values", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: APP_ORIGIN,
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
