import { expect, test, type Page } from "@playwright/test";

const APP_ORIGIN = "http://127.0.0.1:5000";
const KEYCLOAK_AUTH = /127\.0\.0\.1:8080\/realms\/reportcollector\/protocol\/openid-connect\/auth/;

async function loginAs(page: Page, username: string, password: string) {
    await page.goto(APP_ORIGIN + "/", { waitUntil: "domcontentloaded" });
    await page.waitForURL(KEYCLOAK_AUTH, { timeout: 20000 });
    await page.locator("#username").fill(username);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(new RegExp(APP_ORIGIN.replace(".", "\\.")), { timeout: 20000 });
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
}

/**
 * Возвращает DOM-ячейку грида по имени свойства (prop) и индексу строки.
 * Грид виртуализирует колонки по горизонтали: в DOM рендерятся только видимые,
 * поэтому отображаемое число ячеек строки определяется во время выполнения.
 * Работает для обоих пользователей независимо от набора видимых колонок.
 */
async function gridCellByProp(page: Page, prop: string, rowIndex: number) {
    const { flat, visibleCount } = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const cols = await grid.getColumns();
        const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
        const dataRow = [...document.querySelectorAll("revogr-data .rgRow")].find(
            (row) => row.querySelectorAll(".rgCell").length > 1,
        );
        return { flat, visibleCount: dataRow ? dataRow.querySelectorAll(".rgCell").length : 0 };
    });
    const colIndex = flat.findIndex((c) => c.prop === prop);
    expect(colIndex, `колонка ${prop} не найдена`).toBeGreaterThanOrEqual(0);
    expect(colIndex, `колонка ${prop} вне видимой области грида`).toBeLessThan(visibleCount);
    const dataRows = page
        .locator("revogr-data .rgRow")
        .filter({ has: page.locator(".rgCell:nth-child(2)") });
    const cell = dataRows.nth(rowIndex).locator(".rgCell").nth(colIndex);
    await expect(cell).toBeVisible();
    return cell;
}

async function cellValue(page: Page, prop: string, rowIndex: number): Promise<string> {
    return page.evaluate(
        ({ prop, rowIndex }) => {
            const grid = document.querySelector("revo-grid")!;
            return Promise.all([grid.getSource(), grid.getColumns()]).then(([source, cols]) => {
                const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
                const col = flat.find((c) => c.prop === prop);
                return String(source[rowIndex]?.[col?.prop] ?? "");
            });
        },
        { prop, rowIndex },
    );
}

async function editCell(page: Page, cell: ReturnType<typeof page.locator>, value: string) {
    await cell.dblclick();
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    await editor.fill(value);
    await editor.press("Enter");
}

test.describe("collab: совместное редактирование", () => {
    test("скоуп «В сети» по активам: работник ВОС не видит работников СНХЗ", async ({
        browser,
    }) => {
        const ctxA = await browser.newContext();
        const ctxB = await browser.newContext();
        const ctxC = await browser.newContext();
        const pageA = await ctxA.newPage();
        const pageB = await ctxB.newPage();
        const pageC = await ctxC.newPage();
        await loginAs(pageA, "lebedeva", "lebedeva");
        await loginAs(pageB, "smirnov", "smirnov");
        await loginAs(pageC, "evtushenko", "TestPassword123");

        // «В сети:» — жирным, фамилии участников в строке состояния.
        await expect(pageA.locator(".collab-bar b")).toHaveText("В сети:", { timeout: 15000 });
        await expect(pageA.locator(".collab-bar")).toContainText("Вы");

        // Лебедева (только ВОС) на «Движении» видит администратора (Евтушенко)
        // и себя («Вы») — но не Смирнова (СНХЗ).
        await expect(pageA.locator(".collab-bar")).toContainText("Евтушенко");
        await expect(pageA.locator(".collab-bar")).not.toContainText("Смирнов");

        // Смирнов (СНХЗ) видит Евтушенко и себя — но не Лебедеву (ВОС).
        await expect(pageB.locator(".collab-bar")).toContainText("Вы", { timeout: 15000 });
        await expect(pageB.locator(".collab-bar")).toContainText("Евтушенко");
        await expect(pageB.locator(".collab-bar")).not.toContainText("Лебедева");

        // Администратор видит всех.
        await expect(pageC.locator(".collab-bar")).toContainText("Вы", { timeout: 15000 });
        await expect(pageC.locator(".collab-bar")).toContainText("Смирнов");
        await expect(pageC.locator(".collab-bar")).toContainText("Лебедева");

        await ctxA.close();
        await ctxB.close();
        await ctxC.close();
    });

    test("ячейка, занятая другим пользователем, блокируется с предупреждением", async ({
        browser,
    }) => {
        const ctxA = await browser.newContext();
        const ctxB = await browser.newContext();
        const pageA = await ctxA.newPage();
        const pageB = await ctxB.newPage();
        await loginAs(pageA, "smirnov", "smirnov");
        await loginAs(pageB, "petrov", "petrov");

        // Оба пользователя видят одну и ту же ячейку бизнес-плана первого
        // сырья (Алкилат/СНХЗ — доступен обоим).
        const cellA = await gridCellByProp(pageA, "businessPlanTons", 0);
        const cellB = await gridCellByProp(pageB, "businessPlanTons", 0);
        const original = await cellValue(pageA, "businessPlanTons", 0);

        // Пользователь A открывает редактор и держит ячейку (аренда активна).
        await cellA.dblclick();
        const editorA = pageA.locator("revogr-edit input, revogr-edit textarea").first();
        await expect(editorA).toBeVisible({ timeout: 10000 });
        await editorA.fill("999");

        // У пользователя B ячейка помечается занятой: счётчик локов и CSS-класс.
        await expect(pageB.locator(".collab-bar__locks")).toContainText("1", { timeout: 15000 });
        await expect(cellB).toHaveClass(/report-cell--remote-locked/);

        // Попытка B отредактировать занятую ячейку блокируется: редактор не открывается.
        await cellB.dblclick();
        await pageB.waitForTimeout(500);
        await expect(pageB.locator("revogr-edit input, revogr-edit textarea")).toHaveCount(0);
        expect(await cellValue(pageB, "businessPlanTons", 0)).toBe(original);

        // A завершает редактирование — значение применяется у обоих.
        await editorA.press("Enter");
        await expect(pageA.locator("revogr-edit input, revogr-edit textarea")).toHaveCount(0, {
            timeout: 10000,
        });
        await expect
            .poll(() => cellValue(pageA, "businessPlanTons", 0), { timeout: 15000 })
            .toBe("999");
        await expect
            .poll(() => cellValue(pageB, "businessPlanTons", 0), { timeout: 15000 })
            .toBe("999");

        // Аренда снята: B теперь может редактировать ту же ячейку.
        await expect(pageB.locator(".collab-bar__locks")).toHaveCount(0, { timeout: 15000 });
        await editCell(pageB, cellB, "123");
        await expect
            .poll(() => cellValue(pageA, "businessPlanTons", 0), { timeout: 15000 })
            .toBe("123");
        await expect
            .poll(() => cellValue(pageB, "businessPlanTons", 0), { timeout: 15000 })
            .toBe("123");

        // Восстанавливаем исходное значение.
        await editCell(pageA, cellA, original);
        await expect
            .poll(() => cellValue(pageB, "businessPlanTons", 0), { timeout: 15000 })
            .toBe(original);

        await ctxA.close();
        await ctxB.close();
    });
});
