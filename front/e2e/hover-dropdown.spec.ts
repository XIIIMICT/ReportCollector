import { test, expect } from "@playwright/test";

// HoverDropdown regression coverage (see docs/GRID.md «Responsive layout»):
// the dropdown must not flicker while the pointer sits still on the trigger
// (react-aria sets `inert` on the rest of the page while the modal popover is
// open, which would make a mouseleave-based close loop forever), must close on
// an outside click, and must close on Escape without immediately reopening
// (the closing menu un-inerts the trigger and the still-hovering pointer would
// otherwise re-open it). The compact action menu (below sm) and the view
// strip's «Вид таблицы» menu run on the same HoverDropdown.

async function login(page: import("@playwright/test").Page) {
    await page.goto("http://127.0.0.1:5000/", { waitUntil: "domcontentloaded" });
    await page.waitForURL(
        /127\.0\.0\.1:8080\/realms\/reportcollector\/protocol\/openid-connect\/auth/,
        { timeout: 20000 },
    );
    await page.locator("#username").fill("evtushenko");
    await page.locator("#password").fill("TestPassword123");
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/127\.0\.0\.1:5000/, { timeout: 20000 });
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
    // The compact «Действия» menu renders below sm (40rem = 640px).
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
    await expect(menu.getByText("Закрепить заголовок")).toBeVisible();

    // The menu must stay open while the pointer sits still on the button.
    await assertNoFlicker(page, menu, 25);

    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
});
