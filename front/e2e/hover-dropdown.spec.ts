import { test, expect } from "@playwright/test";

import { loginAsTestUser } from "./helpers/test-users";
import { expectHiddenForGrace, expectVisibleForGrace, HOVER_GRACE_MS } from "./helpers/timing";

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
    await loginAsTestUser(page, "admin");
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

async function centerOf(locator: import("@playwright/test").Locator) {
    const box = await locator.boundingBox();
    if (!box) throw new Error("no bounding box");
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function moveAway(page: import("@playwright/test").Page) {
    await page.mouse.move(5, 5);
    await page.mouse.move(3, 3);
}

async function assertNoFlicker(
    page: import("@playwright/test").Page,
    locator: import("@playwright/test").Locator,
    samples: number,
) {
    const states: boolean[] = [];
    for (let i = 0; i < samples; i++) {
        states.push((await locator.count()) > 0);
        // Genuine flicker sampling: an open→close→open cycle can happen and
        // resolve between two web-first assertions, so the pointer must rest
        // while the DOM is sampled repeatedly. No event marks "still still".
        await page.waitForTimeout(100);
    }
    const transitions = states.filter((s, i) => i > 0 && s !== states[i - 1]).length;
    expect(transitions).toBe(0);
}

async function openViewMenu(page: import("@playwright/test").Page) {
    const btn = page.getByRole("button", { name: "Вид таблицы", exact: true });
    await expect(btn).toBeVisible({ timeout: 15000 });
    await hoverWithRealMouse(page, btn);
    const menu = page.locator(".view-strip-popover");
    await expect(menu).toBeVisible({ timeout: 5000 });
    return { btn, menu };
}

test("compact action menu: no flicker on hover, closes on outside click and Escape", async ({
    page,
}) => {
    await login(page);
    // The compact «Действия» menu renders below lg (64rem = 1024px).
    // The visibility assertion below is the boundary: no fixed resize sleep.
    await page.setViewportSize({ width: 600, height: 900 });

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
    // Escape suppression is released only after the pointer leaves and
    // re-enters; sample the whole 250 ms window instead of one arbitrary sleep.
    await expectHiddenForGrace(page, menu, HOVER_GRACE_MS);
});

test("view strip menu: opens on hover, no flicker, Escape closes", async ({ page }) => {
    await login(page);
    const btn = page.getByRole("button", { name: "Вид таблицы", exact: true });
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

test("a real hover opens the menu through React Aria's hover event", async ({ page }) => {
    await login(page);
    const { menu } = await openViewMenu(page);
    await expect(menu).toBeVisible();
});

test("moving from the trigger into the popover on a diagonal keeps it open", async ({ page }) => {
    await login(page);
    const { btn, menu } = await openViewMenu(page);
    const from = await centerOf(btn);
    const to = await centerOf(menu);
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2);
    await page.mouse.move(to.x, to.y);
    // Crossing the trigger→popover gap must survive the full grace window;
    // re-check visibility throughout instead of sleeping once.
    await expectVisibleForGrace(page, menu, HOVER_GRACE_MS);
});

test("outside pointer closes only after the grace period", async ({ page }) => {
    await login(page);
    const { menu } = await openViewMenu(page);
    await moveAway(page);
    // Immediately after leaving both rectangles the menu must still be open.
    await expect(menu).toBeVisible();
    // The one-shot timeout closes it without any further pointer movement.
    await expect(menu).toHaveCount(0, { timeout: 2000 });
});

test("re-entering before the grace period cancels the close", async ({ page }) => {
    await login(page);
    const { btn, menu } = await openViewMenu(page);
    await moveAway(page);
    // Race setup, not absence evidence: re-enter in the first third of the
    // named 250 ms grace window, before the close timer can fire.
    await page.waitForTimeout(HOVER_GRACE_MS / 3);
    const trigger = await centerOf(btn);
    await page.mouse.move(trigger.x, trigger.y);
    await expectVisibleForGrace(page, menu, HOVER_GRACE_MS);
});

test("keyboard opening does not use a stale pointer position", async ({ page }) => {
    await login(page);
    const btn = page.getByRole("button", { name: "Вид таблицы", exact: true });
    await expect(btn).toBeVisible({ timeout: 15000 });
    await moveAway(page);
    await btn.focus();
    // Focus is visible before the menu opens, and Escape puts it back on the
    // trigger — a portal-mounted menu must never keep focus after removal.
    await expect(btn).toHaveAttribute("data-focus-visible", "true");
    await page.keyboard.press("Enter");
    const menu = page.locator(".view-strip-popover");
    await expect(menu).toBeVisible({ timeout: 5000 });
    await expect(btn).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
    await expect(btn).toBeFocused();
    // Re-open for the stale-pointer regression check (the original assertion).
    await page.keyboard.press("Enter");
    await expect(menu).toBeVisible({ timeout: 5000 });
    // A stale pointer must not close the keyboard-opened menu later either.
    await expectVisibleForGrace(page, menu, HOVER_GRACE_MS * 2);
});

test("Escape suppression releases only after the pointer leaves and re-enters", async ({
    page,
}) => {
    await login(page);
    const { btn, menu } = await openViewMenu(page);
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
    await expectHiddenForGrace(page, menu, HOVER_GRACE_MS);
    await moveAway(page);
    await hoverWithRealMouse(page, btn);
    await expect(menu).toBeVisible({ timeout: 5000 });
});

test("a rapid reopen is not closed by a timer from the previous interaction", async ({ page }) => {
    await login(page);
    const { menu } = await openViewMenu(page);
    await moveAway(page);
    await expect(menu).toHaveCount(0, { timeout: 2000 });
    await openViewMenu(page);
    // The previous interaction's timer must not close the fresh popover.
    await expectVisibleForGrace(page, menu, HOVER_GRACE_MS * 2);
});

test.describe("touch interaction", () => {
    test.use({ hasTouch: true });

    test("press opens the menu and a synthetic hover does not close it", async ({ page }) => {
        await login(page);
        const btn = page.getByRole("button", { name: "Вид таблицы", exact: true });
        await expect(btn).toBeVisible({ timeout: 15000 });
        const point = await centerOf(btn);
        await page.touchscreen.tap(point.x, point.y);
        const menu = page.locator(".view-strip-popover");
        await expect(menu).toBeVisible({ timeout: 5000 });
        // A synthetic hover must not trigger the pointer-grace close.
        await expectVisibleForGrace(page, menu, HOVER_GRACE_MS * 2);
    });
});
