import { test, expect } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

// HoverDropdown regression coverage (see docs/GRID.md «Responsive layout»):
// the dropdown must not flicker while the pointer sits still on the trigger
// (react-aria sets `inert` on the rest of the page while the modal popover is
// open, which would make a mouseleave-based close loop forever), must close on
// an outside click, and must close on Escape without immediately reopening
// (the closing menu un-inerts the trigger and the still-hovering pointer would
// otherwise re-open it). The compact action menu (below lg) and the view
// strip's «Вид таблицы» menu run on the same HoverDropdown.

// The shared local-login helper: these suites exercise hover behaviour, not
// SSO, so they must not require a Keycloak redirect to run.
async function login(page: import("@playwright/test").Page) {
    await loginAsLocalUser(page, {
        email: "evtushenko.sn@ruschem.ru",
        password: "TestPassword123",
    });
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30000 });
}

async function hoverWithRealMouse(
    page: import("@playwright/test").Page,
    locator: import("@playwright/test").Locator,
) {
    const box = await locator.boundingBox();
    if (!box) throw new Error("no bounding box");
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    await page.mouse.move(cx - 80, cy);
    await page.mouse.move(cx - 40, cy);
    await page.mouse.move(cx - 10, cy);
    await page.mouse.move(cx, cy);
}

async function assertNoFlicker(
    page: import("@playwright/test").Page,
    locator: import("@playwright/test").Locator,
    samples: number,
) {
    const states: boolean[] = [];
    for (let i = 0; i < samples; i++) {
        states.push((await locator.count()) > 0);
        await page.waitForTimeout(100);
    }
    const transitions = states.filter((s, i) => i > 0 && s !== states[i - 1]).length;
    expect(transitions).toBe(0);
}

test("compact action menu: no flicker on hover, closes on outside click and Escape", async ({
    page,
}) => {
    await login(page);
    // The compact «Действия» menu renders below lg (64rem = 1024px).
    await page.setViewportSize({ width: 600, height: 900 });
    await page.waitForTimeout(1000);

    const btn = page.getByRole("button", { name: "Действия с таблицей" });
    await expect(btn).toBeVisible({ timeout: 15000 });

    await hoverWithRealMouse(page, btn);
    const menu = page.locator(".action-more-menu");
    await expect(menu).toBeVisible({ timeout: 5000 });

    // The menu must stay open while the pointer sits still on the button.
    await assertNoFlicker(page, menu, 30);

    // Outside click closes it.
    await page.mouse.click(30, 200);
    await expect(menu).toHaveCount(0, { timeout: 3000 });

    // Hover again, then Escape closes it without an instant reopen.
    await hoverWithRealMouse(page, btn);
    await expect(menu).toBeVisible({ timeout: 5000 });
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
    await page.waitForTimeout(400);
    await expect(menu).toHaveCount(0);
});

test("view strip menu: opens on hover, no flicker, Escape closes", async ({ page }) => {
    await login(page);
    const btn = page.locator('button[aria-label="Вид таблицы"]');
    await expect(btn).toBeVisible({ timeout: 15000 });

    await hoverWithRealMouse(page, btn);
    const menu = page.locator(".view-strip-popover");
    await expect(menu).toBeVisible({ timeout: 5000 });
    // The entry names the table header («шапка») and flips with the current
    // state, so a pinned header shows «Открепить…».
    await expect(menu.getByText(/шапку таблицы$/)).toBeVisible();

    // The menu must stay open while the pointer sits still on the button.
    await assertNoFlicker(page, menu, 25);

    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
});
