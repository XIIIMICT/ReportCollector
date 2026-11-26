import { expect, test } from "@playwright/test";

import { loginAsTestUser, TEST_USERS } from "./helpers/test-users";

test("evtushenko: raw-materials workspace shows movement, prices and dictionaries", async ({
    page,
}) => {
    await loginAsTestUser(page, "admin");
    // The workspace defaults to «Ремонт»; this spec runs on «Сырьё» forms.
    const switcher = page.locator(".brand__edition--button");
    await expect(switcher).toBeVisible({ timeout: 20000 });
    await switcher.click();
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Сырьё" })
        .click();
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

test("evtushenko: project switcher and dictionary CRUD", async ({ page }) => {
    await loginAsTestUser(page, "admin");
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 20000 });

    // The workspace defaults to «Ремонт» — switch to «Сырьё» first.
    const switcher = page.locator(".brand__edition--button");
    await expect(switcher).toBeVisible();
    await switcher.click();
    await expect(page.locator("[data-slot=dropdown-menu]")).toBeVisible();
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Сырьё" })
        .click();
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });
    await expect(switcher).toContainText("Сырьё");

    // Project switcher: user has access to two projects → dropdown visible.
    await switcher.click();
    await expect(page.locator("[data-slot=dropdown-menu]")).toBeVisible();
    await expect(page.locator("[data-slot=dropdown-menu]")).toContainText("КППК");

    // Switch to КППК → its own workspace (the .project-placeholder is only for
    // a project that still has no UI).
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "КППК" })
        .click();
    await page.waitForURL(/\/kppk/, { timeout: 15000 });
    await expect(page.getByRole("tab", { name: "Реализация", exact: true })).toBeVisible({
        timeout: 20000,
    });

    // Switch back to Сырьё.
    await switcher.click();
    await expect(page.locator("[data-slot=dropdown-menu]")).toBeVisible();
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Сырьё" })
        .click();
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 15000 });

    // Dictionary CRUD: create an asset via the placeholder row.
    await page.getByRole("tab", { name: "Активы" }).click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });
    const uniqueName = `Тест Актив ${Date.now()}`;
    // The placeholder sits at the end of a virtualized list: scroll the grid's
    // internal scroller to the bottom so the row actually renders in the DOM.
    const scrollGridToBottom = () =>
        page.evaluate(() => {
            const scroller = document.querySelector("revo-grid revogr-scroll-virtual.vertical");
            if (scroller) scroller.scrollTop = scroller.scrollHeight;
        });
    await scrollGridToBottom();
    // Target the placeholder row explicitly: with a long asset list, grid
    // virtualization makes dataCells.last() a real row and dblclick would
    // rename it (PATCH) instead of creating a new one (POST).
    const lastCell = page.locator("revogr-data .rgCell.report-cell--placeholder").last();
    await lastCell.waitFor({ state: "visible", timeout: 10000 });
    await lastCell.dblclick();
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible({ timeout: 10000 });
    await editor.fill(uniqueName);
    await editor.press("Enter");
    // After the create the list re-sorts ("Т" sorts last) — scroll down again.
    await scrollGridToBottom();
    await expect(page.locator("revogr-data .rgCell").filter({ hasText: uniqueName })).toBeVisible({
        timeout: 15000,
    });
    // Let the grid finish re-sorting after the create before touching selection.
    await page.waitForTimeout(1500);

    // Select the created row via its row header and delete it through the dialog.
    // The post-create refresh re-sorts and can reset the virtual window to the
    // top: keep scrolling to the bottom until the created row renders.
    const createdCell = page
        .locator("revogr-data .rgCell:not(.report-row-header)")
        .filter({ hasText: uniqueName });
    for (let attempt = 0; attempt < 6; attempt += 1) {
        await scrollGridToBottom();
        if (await createdCell.first().isVisible()) break;
        await page.waitForTimeout(700);
    }
    await expect(createdCell.first()).toBeVisible();
    await page.waitForTimeout(600);
    // The header area and the data area are separate virtual lists; align them by
    // scrolling the header list to the same offset, then click the matching button.
    const targetIndex = await page.evaluate((name) => {
        const cells = [...document.querySelectorAll("revogr-data .rgCell:not(.report-row-header)")];
        const cell = cells.find(
            (candidate) => (candidate as HTMLElement).innerText.trim() === name,
        );
        if (!cell) return -1;
        const dataRow = cell.closest(".rgRow");
        if (!dataRow) return -1;
        const dataIndex = [...dataRow.parentElement!.children].indexOf(dataRow);
        const buttons = [
            ...document.querySelectorAll("revogr-row-headers .report-row-header__button"),
        ];
        return dataIndex < buttons.length ? dataIndex : -1;
    }, uniqueName);
    expect(targetIndex).toBeGreaterThanOrEqual(0);
    await page
        .locator("revo-grid revogr-row-headers .report-row-header__button")
        .nth(targetIndex)
        .click();
    await expect(page.locator("revogr-data .rgCell.report-cell--selected").first()).toHaveCount(1, {
        timeout: 10000,
    });
    // The delete action is named after what it removes («запись» in a
    // dictionary, «строку» in the forms) and is greyed out without a selection.
    await page.getByRole("button", { name: /^Удалить (запись|строку)$/ }).click();
    const deleteDialog = page.getByRole("alertdialog").filter({ hasText: "Удалить запись?" });
    await expect(deleteDialog).toBeVisible({ timeout: 10000 });
    await deleteDialog.getByRole("button", { name: "Удалить" }).click();
    await expect(page.locator("revogr-data .rgCell").filter({ hasText: uniqueName })).toHaveCount(
        0,
        { timeout: 15000 },
    );
});

test("evtushenko: undo/redo works on dictionaries (asset rename)", async ({ page }) => {
    await loginAsTestUser(page, "admin");

    // The workspace defaults to «Ремонт» — switch to «Сырьё» first.
    const switcher = page.locator(".brand__edition--button");
    await expect(switcher).toBeVisible();
    await switcher.click();
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Сырьё" })
        .click();
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
    const headerSwitcher = page.locator(".brand__edition--button");
    await expect(headerSwitcher).toBeVisible();
    await headerSwitcher.click();
    await page
        .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Сырьё" })
        .click();
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
    await expect(page.locator(".theme-selector")).toBeVisible();
    await expect(page.locator('.theme-selector [aria-label="Тёмная тема"]')).toBeVisible();

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
        await page.locator(".profile-modal button", { hasText: "Сохранить" }).click();
        await expect(page.locator(".profile-modal .form-error")).toContainText(
            "Введённые пароли не совпадают.",
        );

        // Matching passwords open the confirmation AlertDialog
        await passwordInputs.nth(1).fill("NewPass123");
        await page.locator(".profile-modal button", { hasText: "Сохранить" }).click();
        await expect(page.getByRole("alertdialog")).toBeVisible({ timeout: 10000 });
        await expect(page.getByRole("alertdialog")).toContainText("Сохранить новый пароль?");

        // Cancel keeps the modal open
        await page.getByRole("alertdialog").getByRole("button", { name: "Отменить" }).click();
        await expect(page.getByRole("alertdialog")).toHaveCount(0);
        await expect(page.locator(".profile-modal")).toBeVisible();
    }

    // Logout flow
    await page.locator(".profile-modal button", { hasText: "Выйти" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("alertdialog")).toContainText("Выйти из приложения?");
    await page.getByRole("alertdialog").getByRole("button", { name: "Выйти" }).last().click();
    // Keycloak sends the browser to the realm; local auth to the login page.
    await page.waitForURL(/\/api\/auth\/(logout|login)|realms\//, { timeout: 20000 });
});
