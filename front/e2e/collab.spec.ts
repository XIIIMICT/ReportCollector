import { expect, test, type Page, type Request } from "@playwright/test";

import { loginAsTestUser, type TestUserId } from "./helpers/test-users";
import {
    EDIT_REJECT_SETTLE_MS,
    expectNoRequestsForGrace,
    expectStableForGrace,
} from "./helpers/timing";

const APP_ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5000";

async function loginAs(page: Page, user: TestUserId) {
    // Deep-link into the raw-materials project: the scenario is about the
    // asset-scoped presence on its «Движение» form. The login lands on the
    // preferred project, so navigate again afterwards.
    await loginAsTestUser(page, user);
    await page.goto(APP_ORIGIN + "/raw-materials", { waitUntil: "domcontentloaded" });
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
}

/**
 * Возвращает DOM-ячейку грида по имени свойства (prop) и индексу строки.
 * Грид виртуализирует колонки по горизонтали: в DOM рендерятся только видимые,
 * поэтому отображаемое число ячеек строки определяется во время выполнения.
 * Работает для обоих пользователей независимо от набора видимых колонок.
 */
async function gridCellByProp(page: Page, prop: string, rowIndex: number) {
    const { flat, pinnedCount, visibleCount } = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const cols = await grid.getColumns();
        const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
        const pinnedCount = flat.filter((column) => column.pin === "colPinStart").length;
        // The pinned viewport renders its own rows; measuring it would compare
        // the full column list against a two-cell row and report a false
        // "outside the visible area" failure.
        const dataRow = document.querySelector("revogr-viewport-scroll.rgCol revogr-data .rgRow");
        return {
            flat,
            pinnedCount,
            visibleCount: dataRow ? dataRow.querySelectorAll(".rgCell").length : 0,
        };
    });
    const colIndex = flat.findIndex((c) => c.prop === prop);
    expect(colIndex, `колонка ${prop} не найдена`).toBeGreaterThanOrEqual(0);
    const visibleIndex = colIndex - pinnedCount;
    expect(visibleIndex, `колонка ${prop} вне видимой области грида`).toBeLessThan(visibleCount);
    const dataRows = page.locator("revogr-viewport-scroll.rgCol revogr-data .rgRow");
    const cell = dataRows.nth(rowIndex).locator(".rgCell").nth(visibleIndex);
    await expect(cell).toBeVisible();
    return cell;
}

async function cellValue(page: Page, prop: string, rowIndex: number): Promise<string> {
    return page.evaluate(
        ({ prop, rowIndex }) => {
            const grid = document.querySelector("revo-grid");
            // null during a grid remount: an empty read lets expect.poll retry.
            if (!grid) return "";
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
    test("скоуп «На форме» по активам: работник ВОС не видит работников СНХЗ", async ({
        browser,
    }) => {
        const ctxA = await browser.newContext();
        const ctxB = await browser.newContext();
        const ctxC = await browser.newContext();
        const pageA = await ctxA.newPage();
        const pageB = await ctxB.newPage();
        const pageC = await ctxC.newPage();
        await loginAs(pageA, "assetWorkerVos");
        await loginAs(pageB, "assetWorkerSnhz");
        await loginAs(pageC, "admin");

        // Assert the actual presence list, independent of tooltip hover timing.
        const presenceA = pageA.locator(".form-status__presence");
        const presenceB = pageB.locator(".form-status__presence");
        const presenceC = pageC.locator(".form-status__presence");
        await expect(presenceC).toContainText("Смирнов");
        await expect(presenceC).toContainText("Лебедева");
        await expect(presenceA).toContainText("Евтушенко");
        await expect(presenceB).toContainText("Евтушенко");
        await expect(presenceA).not.toContainText("Смирнов");
        await expect(presenceB).not.toContainText("Лебедева");
        await expect(presenceA).toContainText("вы");
        await expect(presenceB).toContainText("вы");
        await expect(presenceC).toContainText("вы");

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
        await loginAs(pageA, "assetWorkerSnhz");
        await loginAs(pageB, "assetWorkerBoth");

        // Оба пользователя видят одну и ту же ячейку «План, т» (Потребность)
        // первого сырья (Алкилат/СНХЗ — доступен обоим).
        const cellA = await gridCellByProp(pageA, "demandPlanTons", 0);
        const cellB = await gridCellByProp(pageB, "demandPlanTons", 0);
        const original = await cellValue(pageA, "demandPlanTons", 0);

        // Пользователь A открывает редактор и держит ячейку (аренда активна).
        await cellA.dblclick();
        const editorA = pageA.locator("revogr-edit input, revogr-edit textarea").first();
        await expect(editorA).toBeVisible({ timeout: 10000 });
        await editorA.fill("999");

        // У пользователя B ячейка помечается занятой: CSS-класс подсветки.
        await expect(cellB).toHaveClass(/report-cell--remote-locked/, { timeout: 15000 });

        // Попытка B отредактировать занятую ячейку блокируется: редактор не открывается.
        // A rejected edit must leave BOTH the DOM and the API untouched. The
        // counter catches a late save that a fixed sleep could not see.
        const editRequests: Request[] = [];
        pageB.on("request", (request: Request) => {
            if (
                request.method() === "PATCH" &&
                request.url().includes("/api/raw-materials/movement")
            ) {
                editRequests.push(request);
            }
        });
        await cellB.dblclick();
        await expectStableForGrace(
            pageB,
            () => {
                expect(pageB.locator("revogr-edit input, revogr-edit textarea")).toHaveCount(0);
            },
            EDIT_REJECT_SETTLE_MS,
        );
        await expectNoRequestsForGrace(pageB, editRequests, () => true, EDIT_REJECT_SETTLE_MS);
        expect(await cellValue(pageB, "demandPlanTons", 0)).toBe(original);

        // A завершает редактирование — значение применяется у обоих.
        await editorA.press("Enter");
        await expect(pageA.locator("revogr-edit input, revogr-edit textarea")).toHaveCount(0, {
            timeout: 10000,
        });
        await expect
            .poll(() => cellValue(pageA, "demandPlanTons", 0), { timeout: 15000 })
            .toBe("999");
        await expect
            .poll(() => cellValue(pageB, "demandPlanTons", 0), { timeout: 15000 })
            .toBe("999");

        // Аренда снята: B теперь может редактировать ту же ячейку.
        await expect(cellB).not.toHaveClass(/report-cell--remote-locked/, { timeout: 15000 });
        await editCell(pageB, cellB, "123");
        await expect
            .poll(() => cellValue(pageA, "demandPlanTons", 0), { timeout: 15000 })
            .toBe("123");
        await expect
            .poll(() => cellValue(pageB, "demandPlanTons", 0), { timeout: 15000 })
            .toBe("123");

        // Восстанавливаем исходное значение. B всё ещё держит ячейку выделенной
        // (после Enter фокус остаётся на ней), поэтому сначала уводит выделение
        // (Esc) — иначе ячейка останется занятой для A.
        await pageB.keyboard.press("Escape");
        await expect(cellA).not.toHaveClass(/report-cell--remote-locked/, { timeout: 15000 });
        await editCell(pageA, cellA, original);
        await expect
            .poll(() => cellValue(pageB, "demandPlanTons", 0), { timeout: 15000 })
            .toBe(original);

        await ctxA.close();
        await ctxB.close();
    });

    test("multi-tab: выделение в одной вкладке сбрасывает выделение в другой (один аккаунт)", async ({
        browser,
    }) => {
        // Две вкладки одного аккаунта: на сервере одно присутствие на юзера.
        // Выделение в одной вкладке должно сбросить локальное выделение в другой.
        const ctxA = await browser.newContext();
        const ctxB = await browser.newContext();
        const pageA = await ctxA.newPage();
        const pageB = await ctxB.newPage();
        await loginAs(pageA, "assetWorkerSnhz");
        await loginAs(pageB, "assetWorkerSnhz");

        await expect(pageA.locator(".form-status__dot--online")).toBeVisible();
        await expect(pageB.locator(".form-status__dot--online")).toBeVisible();
        const cellA = await gridCellByProp(pageA, "demandPlanTons", 0);
        const cellB = await gridCellByProp(pageB, "demandPlanTons", 1);

        // 1. Вкладка A выделяет ячейку — в её футере появляется «Выделено».
        await cellA.click();
        await expect(pageA.locator(".form-status__selection")).toContainText("Выделено:", {
            timeout: 15000,
        });

        // 2. Вкладка B выделяет другую ячейку: выделение переходит к B,
        //    у A локальное выделение сбрасывается — строка «Выделено» скрывается
        //    (блок «На форме» при этом остаётся).
        await cellB.click();
        await expect(pageB.locator(".form-status__selection")).toContainText("Выделено:", {
            timeout: 15000,
        });
        // The block is always mounted; an empty selection is marked by the
        // modifier class (and aria-hidden), not by the element disappearing.
        await expect(pageA.locator(".form-status__selection--shown")).toHaveCount(0, {
            timeout: 15000,
        });

        // 3. Обратно: A выделяет — сбрасывается у B.
        await cellA.click();
        await expect(pageA.locator(".form-status__selection")).toContainText("Выделено:", {
            timeout: 15000,
        });
        await expect(pageB.locator(".form-status__selection--shown")).toHaveCount(0, {
            timeout: 15000,
        });

        await ctxA.close();
        await ctxB.close();
    });
});
