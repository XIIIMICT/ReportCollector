import { expect, test } from "@playwright/test";

// Regression coverage for the toolbar hover-open autocompletes/selects
// (version picker, section/asset/block filters):
//  - hovering the trigger opens the popover,
//  - the popover stays open while the pointer moves WITHIN the component and
//    while it sits still (no open/close flicker — HeroUI draws an overlay above
//    the trigger when the popover is open, so structural mouseleave timers loop),
//  - it closes shortly after the pointer leaves the trigger and the popover.

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
}

async function switchToRenovation(page: import("@playwright/test").Page) {
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 30000 });
    const switcher = page.locator(".brand__edition--button");
    await expect(switcher).toBeVisible();
    if (!(await switcher.innerText()).includes("Ремонт")) {
        await switcher.click();
        await expect(page.locator("[data-slot=dropdown-menu]")).toBeVisible();
        await page
            .locator("[data-slot=dropdown-menu] [data-slot=menu-item]", { hasText: "Ремонт" })
            .click();
    }
    await expect(page.locator("revo-grid").first()).toBeVisible({ timeout: 40000 });
}

test("version autocomplete: hover opens, stays open moving within, closes after leaving", async ({
    page,
}) => {
    await login(page);
    await switchToRenovation(page);
    await page.waitForTimeout(1000);

    const trigger = page.locator(".archive-autocomplete").first().locator("button").first();
    await trigger.scrollIntoViewIfNeeded();
    const box = await trigger.boundingBox();
    expect(box).not.toBeNull();
    const cx = box!.x + box!.width / 2;
    const cy = box!.y + box!.height / 2;

    // Open by hovering the trigger.
    await page.mouse.move(cx, cy);
    await expect(trigger).toHaveAttribute("aria-expanded", "true", { timeout: 5000 });

    // Must STAY open while moving within the component (10 scattered moves).
    for (let i = 0; i < 10; i++) {
        const ox = cx + ((i % 3) - 1) * 4;
        const oy = cy + (((i + 1) % 3) - 1) * 3;
        await page.mouse.move(ox, oy, { steps: 1 });
        await page.waitForTimeout(100);
        expect(await trigger.getAttribute("aria-expanded"), `closed at move ${i}`).toBe("true");
    }
    // And stays open while the pointer rests (no flicker).
    const samples: boolean[] = [];
    for (let i = 0; i < 20; i++) {
        samples.push((await trigger.getAttribute("aria-expanded")) === "true");
        await page.waitForTimeout(80);
    }
    const transitions = samples.filter((s, i) => i > 0 && s !== samples[i - 1]).length;
    expect(transitions).toBe(0);

    // Moving well away closes it shortly after.
    await page.mouse.move(5, 5);
    await expect(trigger).toHaveAttribute("aria-expanded", "false", { timeout: 6000 });
});

test("action-bar kbd hint appears in the viewport without a page scrollbar", async ({ page }) => {
    // Regression: the hint must not be positioned against the footer (whose
    // `contain: paint` makes it the containing block for `fixed` children, so
    // an in-place fixed hint rendered off-screen), and it must not grow the
    // document (the old react-aria tooltip portaled an `absolute` box into the
    // body, flashing a stray page scrollbar).
    await login(page);
    await switchToRenovation(page);
    await page.waitForTimeout(1000);

    const before = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: document.documentElement.clientHeight,
    }));

    const copy = page.locator('.action-bar [data-action-tooltip="Копировать"]').first();
    const box = await copy.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);

    const hint = page.locator(".action-bar__hint");
    await expect(hint).toBeVisible({ timeout: 3000 });
    await expect(hint).toContainText("Копировать");
    // The shortcut is a real Kbd component (modifier glyph + key), not text.
    await expect(hint.locator(".kbd__abbr").first()).toBeAttached();
    await expect(hint.locator(".kbd__content").first()).toHaveText("C");
    // Portaled to <body> (escapes the footer's containing block) and fixed.
    expect(await hint.evaluate((el) => el.parentElement?.tagName)).toBe("BODY");
    expect(await hint.evaluate((el) => getComputedStyle(el).position)).toBe("fixed");

    const viewport = page.viewportSize()!;
    const hintBox = await hint.boundingBox();
    expect(hintBox).not.toBeNull();
    expect(hintBox!.x).toBeGreaterThanOrEqual(0);
    expect(hintBox!.y).toBeGreaterThanOrEqual(0);
    expect(hintBox!.x + hintBox!.width).toBeLessThanOrEqual(viewport.width);
    expect(hintBox!.y + hintBox!.height).toBeLessThanOrEqual(viewport.height);

    // No scrollbar appears while the hint is open.
    const after = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: document.documentElement.clientHeight,
    }));
    expect(after.scrollWidth).toBeLessThanOrEqual(after.clientWidth);
    expect(after.scrollHeight).toBeLessThanOrEqual(after.clientHeight);
    expect(after).toEqual(before);
});
