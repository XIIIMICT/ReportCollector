import { test, expect } from "@playwright/test";

// HoverDropdown regression coverage (see docs/GRID.md «Responsive layout»):
// the dropdown must not flicker while the pointer sits still on the trigger
// (react-aria sets `inert` on the rest of the page while the modal popover is
// open, which would make a mouseleave-based close loop forever), must close on
// an outside click, and must close on Escape without immediately reopening
// (the closing menu un-inerts the trigger and the still-hovering pointer would
// otherwise re-open it). Both the compact action menu and the grid corner
// options menu run on the same HoverDropdown.

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
    // Compact mode kicks in below lg (64rem = 1024px).
    await page.setViewportSize({ width: 900, height: 700 });
    await page.waitForTimeout(1000);
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

test("compact action menu: no flicker on hover, closes on outside click and Escape", async ({
    page,
}) => {
    await login(page);
    const btn = page.locator(".action-more-button");
    await expect(btn).toBeVisible({ timeout: 15000 });

    await hoverWithRealMouse(page, btn);
    await expect(page.locator(".action-more-menu")).toBeVisible({ timeout: 5000 });

    // The menu must stay open while the pointer sits still on the button.
    const samples: boolean[] = [];
    for (let i = 0; i < 30; i++) {
        samples.push((await page.locator(".action-more-menu").count()) > 0);
        await page.waitForTimeout(100);
    }
    const transitions = samples.filter((s, i) => i > 0 && s !== samples[i - 1]).length;
    expect(transitions).toBe(0);

    // Outside click closes it.
    await page.mouse.click(30, 200);
    await expect(page.locator(".action-more-menu")).toHaveCount(0, { timeout: 3000 });

    // Hover again, then Escape closes it without an instant reopen.
    await hoverWithRealMouse(page, btn);
    await expect(page.locator(".action-more-menu")).toBeVisible({ timeout: 5000 });
    await page.keyboard.press("Escape");
    await expect(page.locator(".action-more-menu")).toHaveCount(0, { timeout: 3000 });
    await page.waitForTimeout(400);
    await expect(page.locator(".action-more-menu")).toHaveCount(0);
});

test("grid corner options menu: opens on hover, no flicker, items toggle, Escape closes", async ({
    page,
}) => {
    await login(page);
    const btn = page.locator('[aria-label="Настройки таблицы"]');
    await expect(btn).toBeVisible({ timeout: 15000 });

    await hoverWithRealMouse(page, btn);
    await expect(page.locator(".grid-options-popover")).toBeVisible({ timeout: 5000 });

    const samples: boolean[] = [];
    for (let i = 0; i < 25; i++) {
        samples.push((await page.locator(".grid-options-popover").count()) > 0);
        await page.waitForTimeout(100);
    }
    const transitions = samples.filter((s, i) => i > 0 && s !== samples[i - 1]).length;
    expect(transitions).toBe(0);

    // A multi-select menu stays open on item click; the checked state flips.
    const item = page.locator('.grid-options-popover [data-key="pin-header"]');
    const before = await item.getAttribute("aria-checked");
    await item.click();
    await expect(page.locator(".grid-options-popover")).toHaveCount(1);
    await expect(item).toHaveAttribute("aria-checked", before === "true" ? "false" : "true");

    await page.keyboard.press("Escape");
    await expect(page.locator(".grid-options-popover")).toHaveCount(0, { timeout: 3000 });
});
