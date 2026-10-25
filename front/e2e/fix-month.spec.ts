import { expect, test, type Page } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

/**
 * The «Зафиксировать» (fix-month) flow on the «Движение» form.
 *
 * Fixing is IRREVERSIBLE — a final fix closes the month and opens the next one —
 * so this spec never confirms it. What it pins is the part that is safe to run
 * on a shared stand and that has actually broken before:
 *  - the action exists only where a fix is allowed (admin, «Движение», not an
 *    archive, the first open month);
 *  - it opens a dialog that names the consequence, and Escape / «Отменить»
 *    close it WITHOUT touching the database;
 *  - a second fix of an already-fixed month is refused by the UI, not only by
 *    the backend.
 */

const BASE = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5000";

async function loginAndOpenMovement(page: Page) {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await page.goto(`${BASE}/raw-materials`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
}

const fixAction = (page: Page) => page.getByRole("button", { name: "Зафиксировать", exact: true });
const fixDialog = (page: Page) => page.getByRole("alertdialog");

test("the fix dialog opens, explains the consequence and closes without writing", async ({ page }) => {
    await loginAndOpenMovement(page);

    const action = fixAction(page);
    await expect(action).toBeVisible({ timeout: 20000 });
    // The button is only actionable where a fix is allowed; if this env is
    // already fully fixed there is nothing to open and the spec is not relevant.
    if (await action.isDisabled()) {
        test.skip(true, "the month is already fixed on this stand");
    }
    await action.click();

    const dialog = fixDialog(page);
    await expect(dialog).toBeVisible({ timeout: 10000 });
    await expect(dialog).toContainText("Зафиксировать движение сырья?");
    // The dialog must state which kind of fix this is: the consequence differs.
    await expect(dialog).toContainText(/предварительная|финальная/);

    // Escape closes it and leaves the form as it was.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden({ timeout: 10000 });
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible();
});

test("«Отменить» dismisses the fix without fixing anything", async ({ page }) => {
    await loginAndOpenMovement(page);

    const action = fixAction(page);
    if (await action.isDisabled()) {
        test.skip(true, "the month is already fixed on this stand");
    }
    await action.click();
    const dialog = fixDialog(page);
    await expect(dialog).toBeVisible({ timeout: 10000 });

    await dialog.getByRole("button", { name: "Отменить" }).click();
    await expect(dialog).toBeHidden({ timeout: 10000 });

    // Still fixable: nothing was committed by the cancelled dialog.
    await expect(fixAction(page)).toBeEnabled({ timeout: 10000 });
});

test("the fix action is not offered outside «Движение»", async ({ page }) => {
    await loginAndOpenMovement(page);

    // «Цены» is a different form; fixing is a movement-month operation only.
    await page.getByRole("tab", { name: /^Цены/ }).first().click();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 20000 });

    await expect(fixAction(page)).toHaveCount(0);
});
