import { expect, test } from "@playwright/test";

import { loginAsTestUser } from "./helpers/test-users";

// Regression coverage for the toolbar hover-open autocompletes/selects
// (version picker, section/asset/block filters):
//  - hovering the trigger opens the popover,
//  - the popover stays open while the pointer moves WITHIN the component and
//    while it sits still (no open/close flicker — HeroUI draws an overlay above
//    the trigger when the popover is open, so structural mouseleave timers loop),
//  - it closes shortly after the pointer leaves the trigger and the popover.

// The shared local-login helper: these suites exercise hover behaviour, not
// SSO, so they must not require a Keycloak redirect to run.
async function login(page: import("@playwright/test").Page) {
    await loginAsTestUser(page, "admin");
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

    // The isolated seed provisions an archived plan version, so the picker is
    // always present here — assert it rather than skipping when it is absent.
    const picker = page.locator(".archive-autocomplete--visible").first();
    await expect(picker).toBeVisible({ timeout: 15000 });
    const trigger = picker.locator("button").first();
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
    // A shortcut hint carries the KEYS only: the label is already on the
    // button, so repeating it here was deliberately dropped.
    await expect(hint.locator(".action-bar__hint-keys")).toContainText("Ctrl");
    // The shortcut is a real Kbd component. HeroUI v3 renders plain `<kbd>`
    // elements with written-out modifiers, not the v2 `.kbd__abbr`/`.kbd__content`
    // glyph pair, so assert the rendered keys rather than the old DOM internals.
    await expect(hint.locator("kbd")).toHaveCount(2);
    await expect(hint.locator("kbd").nth(0)).toHaveText("Ctrl");
    await expect(hint.locator("kbd").nth(1)).toHaveText("C");
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
