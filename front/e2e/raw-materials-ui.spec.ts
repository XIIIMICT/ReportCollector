import { expect, test, type Page } from "@playwright/test";

import { loginAsTestUser, TEST_USERS } from "./helpers/test-users";
import { expectStableForGrace, GRID_SETTLE_MS } from "./helpers/timing";

const projectMenu = (page: Page) => page.locator("[data-slot=dropdown-menu]");
const projectSwitcher = (page: Page) =>
    page.getByRole("button", { name: /^Проект: .+\. Выберите проект$/ });

/** Open the brand project menu via keyboard (no pointer-grace close timer). */
async function switchProject(page: Page, name: string): Promise<void> {
    const menu = projectMenu(page);
    if (await menu.isVisible()) {
        await page.keyboard.press("Escape");
        await expect(menu).toHaveCount(0, { timeout: 5000 });
    }
    const switcher = projectSwitcher(page);
    await switcher.focus();
    await switcher.press("Enter");
    await expect(menu).toBeVisible({ timeout: 10000 });
    // HeroUI's single-selection Dropdown.Item is a menuitemradio, not menuitem.
    const item = menu.getByRole("menuitemradio", { name, exact: true });
    await expect(item).toBeVisible({ timeout: 10000 });
    await item.click();
    await expect(projectSwitcher(page)).toContainText(name);
}

test("evtushenko: raw-materials workspace shows movement, prices and dictionaries", async ({
    page,
}) => {
    await loginAsTestUser(page, "admin");
    // The workspace defaults to «Ремонт»; this spec runs on «Сырьё» forms.
    await expect(projectSwitcher(page)).toBeVisible({ timeout: 20000 });
    await switchProject(page, "Сырьё");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    // Сид: справочники + связки актив-сырьё, цены и движения генерируются из связок.
    const movementTab = page.getByRole("tab", { name: /Движение за / });
    await expect(movementTab.first()).toBeVisible({ timeout: 20000 });
    await movementTab.first().click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const movementCells = await page.locator("revogr-data .rgCell").count();
    console.log(`movement cells: ${movementCells}`);
    expect(movementCells).toBeGreaterThan(0);

    await expect(page.getByRole("tab", { name: "Цены" })).toBeVisible();
    await page.getByRole("tab", { name: "Цены" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const priceCells = await page.locator("revogr-data .rgCell").count();
    console.log(`price cells: ${priceCells}`);
    expect(priceCells).toBeGreaterThan(0);

    await page.getByRole("tab", { name: "Сырьевые позиции" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const materialCells = await page.locator("revogr-data .rgCell").count();
    console.log(`material cells: ${materialCells}`);
    expect(materialCells).toBeGreaterThan(0);

    await page.getByRole("tab", { name: "Активы" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const assetCells = await page.locator("revogr-data .rgCell").count();
    console.log(`asset cells: ${assetCells}`);
    expect(assetCells).toBeGreaterThan(0);

    await page.getByRole("tab", { name: "Базисы поставки" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    await page.getByRole("tab", { name: "Пользователи" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const userCells = await page.locator("revogr-data .rgCell").count();
    console.log(`user cells: ${userCells}`);
    expect(userCells).toBeGreaterThan(0);
});

test("project switcher exposes both projects", async ({ page }) => {
    await loginAsTestUser(page, "admin");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    await switchProject(page, "Сырьё");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });

    const menu = projectMenu(page);
    if (!(await menu.isVisible())) {
        const switcher = projectSwitcher(page);
        await switcher.focus();
        await switcher.press("Enter");
    }
    await expect(menu).toContainText("КППК");
    await page.keyboard.press("Escape");
});

test("dictionary asset can be created and deleted", async ({ page }) => {
    await loginAsTestUser(page, "admin");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });
    // ONE project switch: switching away and back leaves the collab/project
    // session in a state where the placeholder editor never posts.
    await switchProject(page, "Сырьё");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });

    await page.getByRole("tab", { name: "Активы" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const uniqueName = `Тест Актив ${Date.now()}`;
    const scrollGridToBottom = () =>
        page.evaluate(() => {
            document
                .querySelectorAll("revo-grid revogr-scroll-virtual.vertical")
                .forEach((scroller) => {
                    scroller.scrollTop = scroller.scrollHeight;
                });
        });
    const placeholder = page.locator("revogr-data .rgCell.report-cell--placeholder").last();
    await expect
        .poll(
            async () => {
                await scrollGridToBottom();
                return placeholder.isVisible();
            },
            { timeout: 15000 },
        )
        .toBe(true);
    // Target the draft by its stable fake id, not by a rendered cell: a refresh
    // arriving between click and commit can leave the editor attached to a real
    // row at the same virtual index (which then PATCHes instead of creating).
    const readPlaceholderIndex = () =>
        page.evaluate(async () => {
            const grid = document.querySelector("revo-grid");
            if (!grid) return -1;
            const rows = await grid.getVisibleSource("rgRow");
            return rows.findIndex((row) => row && row.id === "new:assets");
        });
    await expect.poll(readPlaceholderIndex, { timeout: 15000 }).toBeGreaterThanOrEqual(0);
    const placeholderIndex = await readPlaceholderIndex();
    await expectStableForGrace(
        page,
        async () => {
            expect(await readPlaceholderIndex()).toBe(placeholderIndex);
        },
        GRID_SETTLE_MS,
    );
    await page.evaluate(async (index) => {
        const grid = document.querySelector("revo-grid")!;
        await grid.setCellEdit(index, "name");
    }, placeholderIndex);
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    // The editor mounts before it accepts input; filling it too early leaves
    // the draft empty and Enter commits nothing (no POST at all).
    await expect(editor).toBeFocused();
    await editor.fill(uniqueName);
    await expect(editor).toHaveValue(uniqueName);
    const createResponse = page.waitForResponse(
        (response) =>
            response.request().method() === "POST" &&
            response.url().includes("/api/raw-materials/assets"),
        { timeout: 15000 },
    );
    await editor.press("Enter");
    const response = await createResponse;
    expect(response.ok()).toBe(true);
    const created = (await response.json()) as { id?: string };
    const createdId = created.id;
    expect(createdId).toBeTruthy();
    const origin = new URL(page.url()).origin;
    const sessionId = new URL(response.url()).searchParams.get("session_id");

    try {
        // Re-open the section so the created record is a real row, not the
        // placeholder draft still showing the typed text.
        await page.getByRole("tab", { name: "Активы" }).click();
        const createdCell = page
            .locator("revogr-data .rgCell:not(.report-cell--placeholder)")
            .filter({ hasText: uniqueName });
        await expect(createdCell.first()).toBeVisible({ timeout: 15000 });
        const rowIndex = await page.evaluate((name) => {
            const cells = [
                ...document.querySelectorAll("revogr-data .rgCell:not(.report-cell--placeholder)"),
            ];
            const cell = cells.find((candidate) =>
                (candidate as HTMLElement).innerText.includes(name),
            );
            const value = cell?.getAttribute("data-rgrow");
            return value === null || value === undefined ? null : Number(value);
        }, uniqueName);
        expect(rowIndex).not.toBeNull();
        // RevoGrid keeps the row-number button in a separate virtual viewport.
        // Both areas expose the same `data-rgrow`, which is stable across
        // virtualization (unlike a positional index into rendered children).
        await page.locator(`revogr-row-headers .rgCell[data-rgrow="${rowIndex}"] button`).click();
        await expect(page.locator("revogr-data .rgCell.report-cell--selected").first()).toHaveCount(
            1,
            { timeout: 10000 },
        );
        await page.getByRole("button", { name: /^Удалить (запись|строку)$/ }).click();
        const dialog = page.getByRole("alertdialog").filter({ hasText: "Удалить запись?" });
        await expect(dialog).toBeVisible({ timeout: 10000 });
        await dialog.getByRole("button", { name: "Удалить" }).click();
        await expect(
            page.locator("revogr-data .rgCell").filter({ hasText: uniqueName }),
        ).toHaveCount(0, { timeout: 15000 });
    } finally {
        // A failed assertion must not leave an orphan that distorts the next
        // retry's virtualized geometry.
        const query = sessionId ? `?session_id=${sessionId}` : "";
        await page.request.delete(`${origin}/api/raw-materials/assets/${createdId}${query}`);
    }
});

test("evtushenko: undo/redo works on dictionaries (asset rename)", async ({ page }) => {
    await loginAsTestUser(page, "admin");

    // The workspace defaults to «Ремонт» — switch to «Сырьё» first.
    await expect(projectSwitcher(page)).toBeVisible();
    await switchProject(page, "Сырьё");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });

    await page.getByRole("tab", { name: "Активы" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    // The tab switch remounts the grid (a loading window has no revo-grid):
    // readName returns null during that window and expect.poll retries it.
    const readName = (): Promise<string | null> =>
        page.evaluate(() => {
            const grid = document.querySelector("revo-grid");
            if (!grid) return Promise.resolve(null);
            return Promise.all([grid.getSource(), grid.getColumns()]).then(([source, cols]) => {
                const flat = cols.flatMap((c) => (c.children?.length ? c.children : [c]));
                const col = flat.find((c) => c.prop === "name");
                return String(source[0]?.[col?.prop] ?? "");
            });
        });
    let original: string | null = null;
    await expect
        .poll(
            async () => {
                original = await readName();
                return original;
            },
            { timeout: 15000 },
        )
        .not.toBeNull();
    const temporary = `${original} (временный)`;

    const firstCell = page.locator("revogr-data .rgCell:not(.report-row-header)").first();
    await firstCell.dblclick();
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    await editor.fill(temporary);
    await editor.press("Enter");
    await expect.poll(() => readName(), { timeout: 15000 }).toBe(temporary);
    // The source can update before the cell editor unmounts. While it is still
    // mounted, Ctrl+Z belongs to the text editor, not the workspace undo
    // shortcut — wait for the editor to close before sending the hotkey.
    await expect(editor).toHaveCount(0, { timeout: 10000 });

    // Ctrl+Z отменяет правку справочника (через event.code — при любой раскладке).
    await page.keyboard.press("Control+z");
    await expect.poll(() => readName(), { timeout: 15000 }).toBe(original);

    // Ctrl+Y возвращает её.
    await page.keyboard.press("Control+y");
    await expect.poll(() => readName(), { timeout: 15000 }).toBe(temporary);

    // Возвращаем исходное имя.
    await page.keyboard.press("Control+z");
    await expect.poll(() => readName(), { timeout: 15000 }).toBe(original);
});

test("evtushenko: header shows fullname, profile modal works with theme + password", async ({
    page,
}) => {
    await loginAsTestUser(page, "admin");

    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    // The workspace defaults to «Ремонт» — the header shows the active
    // project edition, so switch to «Сырьё» for the brand assertion.
    await expect(projectSwitcher(page)).toBeVisible();
    await switchProject(page, "Сырьё");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });

    // Header: brand with project edition, profile pill shows ФИО + email (not email twice)
    const brand = page.locator(".brand");
    await expect(brand).toContainText("РепортКоллектор");
    await expect(brand).toContainText("Сырьё");

    const pill = page.locator(".profile-pill");
    await expect(pill).toContainText("Евтушенко Сергей Николаевич");
    await expect(pill).toContainText(TEST_USERS.admin.email);

    // No theme switcher in the header anymore
    await expect(page.locator(".app-header .theme-selector")).toHaveCount(0);

    // Open profile modal
    await page.locator(".profile-pill").click();
    await expect(page.locator(".profile-modal")).toBeVisible({ timeout: 10000 });

    // Theme selector inside the modal (ToggleButtonGroup)
    const themeSelector = page.locator(".theme-selector");
    await expect(themeSelector).toBeVisible();
    await expect(
        // ToggleButtonGroup with single selection exposes its options as radios.
        themeSelector.getByRole("radio", { name: "Тёмная тема", exact: true }),
    ).toBeVisible();

    // ФИО and email displayed (read-only)
    await expect(page.locator(".profile-modal input").first()).toHaveValue(
        "Евтушенко Сергей Николаевич",
    );

    // Password fields exist only in local-auth mode: with Keycloak the
    // passwords are managed there and the modal hides the fields.
    // Relative to the configured baseURL: a hardcoded :5000 made this branch
    // read the dev stand's config while the rest of the spec ran elsewhere.
    const authConfig = await page.request
        .get("/api/auth/config")
        .then((response) => response.json() as Promise<{ keycloakEnabled: boolean }>);
    if (!authConfig.keycloakEnabled) {
        // Password fields are editable
        const passwordInputs = page.locator('.profile-modal input[type="password"]');
        await expect(passwordInputs).toHaveCount(2);
        await expect(passwordInputs.nth(0)).toBeEnabled();

        // Mismatched passwords show validation error
        await passwordInputs.nth(0).fill("NewPass123");
        await passwordInputs.nth(1).fill("Different456");
        await page
            .getByRole("dialog")
            .getByRole("button", { name: "Сохранить", exact: true })
            .click();
        await expect(page.locator(".profile-modal .form-error")).toContainText(
            "Введённые пароли не совпадают.",
        );

        // Matching passwords open the confirmation AlertDialog
        await passwordInputs.nth(1).fill("NewPass123");
        await page
            .getByRole("dialog")
            .getByRole("button", { name: "Сохранить", exact: true })
            .click();
        await expect(page.getByRole("alertdialog")).toBeVisible({ timeout: 10000 });
        await expect(page.getByRole("alertdialog")).toContainText("Сохранить новый пароль?");

        // Cancel keeps the modal open
        await page.getByRole("alertdialog").getByRole("button", { name: "Отменить" }).click();
        await expect(page.getByRole("alertdialog")).toHaveCount(0);
        await expect(page.locator(".profile-modal")).toBeVisible();
    }

    // Logout flow
    await page.getByRole("dialog").getByRole("button", { name: "Выйти", exact: true }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("alertdialog")).toContainText("Выйти из приложения?");
    await page.getByRole("alertdialog").getByRole("button", { name: "Выйти" }).last().click();
    // Keycloak sends the browser to the realm; local auth to the login page.
    await page.waitForURL(/\/api\/auth\/(logout|login)|realms\//, { timeout: 20000 });
});
