import { expect, test } from "@playwright/test";

import { loginAsTestUser } from "./helpers/test-users";

test("reduced-motion disables app-owned transitions", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAsTestUser(page, "admin");
    await page.goto("/");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30000 });
    const styles = await page.evaluate(() => {
        const node = document.querySelector(".app-header");
        if (!node) throw new Error("app-header is missing");
        const style = getComputedStyle(node);
        return { transition: style.transitionDuration, animation: style.animationName };
    });
    expect(styles.transition).toBe("0s");
    expect(styles.animation).toBe("none");
});

test("reduced motion makes every app-owned animated surface instant", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAsTestUser(page, "admin");
    await page.goto("/renovation");
    await expect(page.locator(".renovation-grid__header-slot")).toBeVisible({ timeout: 30000 });

    const surfaces = await page.evaluate(() => {
        const selectors = [
            "html",
            ".app-header",
            ".app-body",
            ".table-toolbar",
            ".table-footer",
            ".form-status__selection",
            ".toolbar-collapse",
            ".archive-autocomplete",
            ".action-bar > *",
            ".expandable-button",
            ".expandable-button__label",
            ".renovation-grid__header-slot",
            ".renovation-grid__header-cell",
            ".renovation-grid__header-label",
            ".renovation-grid__number-label",
            ".renovation-grid__corner-label",
            ".header-filter-button",
            ".report-header__title",
        ];
        return selectors.flatMap((selector) => {
            const node = document.querySelector(selector);
            if (!node) return [];
            const style = getComputedStyle(node);
            return [
                {
                    selector,
                    transitions: style.transitionDuration.split(",").map((value) => value.trim()),
                    animation: style.animationName,
                },
            ];
        });
    });
    // The selector list is deliberately broader than the DOM on one route: a
    // dropped rule must fail here, not silently pass because its node was absent.
    expect(surfaces.length).toBeGreaterThanOrEqual(15);
    for (const surface of surfaces) {
        expect(
            surface.transitions.every((value) => value === "0s"),
            surface.selector,
        ).toBe(true);
        expect(surface.animation, surface.selector).toBe("none");
    }
});

test("action-bar controls keep accessible names and native disabled semantics", async ({
    page,
}) => {
    await loginAsTestUser(page, "admin");
    await page.goto("/renovation");
    await expect(page.locator(".action-bar")).toBeVisible({ timeout: 30000 });
    const controls = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLButtonElement>(".action-bar button")].map((button) => ({
            label: button.getAttribute("aria-label"),
            disabled: button.disabled,
            tabIndex: button.getAttribute("tabindex"),
        })),
    );
    expect(controls.length).toBeGreaterThan(3);
    for (const control of controls) {
        expect(control.label, "every action needs an accessible name").toBeTruthy();
        // HeroUI uses the native disabled state; `aria-disabled` is optional.
        // Disabled controls stay out of the tab order (no explicit positive
        // tabindex); enabled ones keep the natural one.
        if (control.disabled) {
            expect(control.tabIndex === null || control.tabIndex === "-1").toBe(true);
        } else {
            expect(control.tabIndex === null || control.tabIndex === "0").toBe(true);
        }
    }
});

test("reduced motion suppresses HeroUI overlay keyframes and keeps keyboard focus", async ({
    page,
}) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAsTestUser(page, "admin");
    await page.goto("/renovation");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30000 });
    // Capture at the source: computed `animation-name` at an arbitrary sample
    // time would miss a 100-150 ms enter/exit keyframe entirely.
    await page.evaluate(() => {
        type AnimationWindow = Window & { __overlayAnimations?: string[] };
        const target = window as unknown as AnimationWindow;
        target.__overlayAnimations = [];
        document.addEventListener(
            "animationstart",
            (event) => {
                const name = (event as AnimationEvent).animationName;
                if (name.startsWith("heroui-overlay-")) target.__overlayAnimations?.push(name);
            },
            true,
        );
    });
    const button = page.getByRole("button", { name: "Вид таблицы", exact: true });
    await expect(button).toBeVisible({ timeout: 15000 });
    await button.focus();
    await expect(button).toHaveAttribute("data-focus-visible", "true");
    await page.keyboard.press("Enter");
    const menu = page.locator(".view-strip-popover");
    await expect(menu).toBeVisible({ timeout: 5000 });
    await expect(button).toHaveAttribute("aria-expanded", "true");
    const entered = await page.evaluate(
        () => (window as unknown as { __overlayAnimations?: string[] }).__overlayAnimations ?? [],
    );
    expect(entered).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0, { timeout: 3000 });
    // Dismissal must return focus to the trigger and leave no focus inside the
    // portal that React Aria removed.
    await expect(button).toBeFocused();
    expect(
        await page.evaluate(() =>
            [
                ...document.querySelectorAll("[data-slot='popover'], [data-slot='dropdown-menu']"),
            ].some((node) => node.contains(document.activeElement)),
        ),
    ).toBe(false);
});

test("renovation single-cell edit persists and Ctrl+A stays disabled", async ({ page }) => {
    await loginAsTestUser(page, "admin");
    await page.goto("/renovation");
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    // The first chunk arrives asynchronously: take the row once it is loaded.
    // The plan OPENS AT ITS TAIL (rows are appended at the end), so this is not
    // «№» 1 — the real position is kept alongside the id.
    await expect
        .poll(
            () =>
                page.evaluate(async () => {
                    const grid = document.querySelector("revo-grid");
                    if (!grid) return 0;
                    const rows = await grid.getVisibleSource("rgRow");
                    return rows.filter((row) => row && row.id).length;
                }),
            { timeout: 20000 },
        )
        .toBeGreaterThan(0);
    const target = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const rows = await grid.getVisibleSource("rgRow");
        const row = rows.find((candidate) => candidate && candidate.id);
        return { rowId: String(row.id), position: Number(row.__position ?? 1) };
    });
    const rowId = target.rowId;
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const columnIndex = columns.findIndex((column) => column.prop === "process_position");
        if (columnIndex < 0) throw new Error("Missing process_position");
        const rows = await grid.getVisibleSource("rgRow");
        const rowIndex = rows.findIndex((row) => row && row.id);
        // `setCellEdit` opens the editor directly — on revo-grid 4.31 the old
        // `setCellsFocus(...)` + `Enter` left focus on the body and never mounted
        // `revogr-edit`. Both calls share ONE evaluate: scrolling re-renders the
        // grid and would drop the editor's focus.
        await grid.scrollToColumnIndex(columnIndex);
        await grid.setCellEdit(rowIndex, "process_position");
    });
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    // The editor mounts before it accepts input: type only once it holds focus,
    // or `fill` lands nowhere and Enter commits the unchanged value.
    await expect(editor).toBeFocused();
    // A fresh value per run: the grid skips a commit when the text equals what
    // is already stored.
    const value = `CI renovation ${Date.now()}`;
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/renovation/plan?") &&
            response.request().method() === "PATCH",
        { timeout: 15000 },
    );
    await editor.fill(value);
    await expect(editor).toHaveValue(value);
    await editor.press("Enter");
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    // The grid re-opens at its remembered (or tail) position: jump to the row's
    // «№» first, then read it back by id.
    await page.evaluate(async (position) => {
        const grid = document.querySelector("revo-grid")!;
        await grid.scrollToRow(position - 1);
    }, target.position);
    await expect
        .poll(
            () =>
                page.evaluate(async (id) => {
                    const grid = document.querySelector("revo-grid");
                    if (!grid) return null;
                    const row = (await grid.getVisibleSource("rgRow")).find(
                        (candidate) => candidate.id === id,
                    );
                    return row ? (row.process_position ?? "") : null;
                }, rowId),
            { timeout: 25000 },
        )
        .toBe(value);
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const index = columns.findIndex((column) => column.prop === "process_position");
        await grid.setCellsFocus({ x: index, y: 0 }, { x: index, y: 0 });
        await grid.refresh();
    });
    const readRange = () =>
        page.evaluate(() => document.querySelector("revo-grid")!.getSelectedRange());
    const before = await readRange();
    expect(before).not.toBeNull();
    await page.keyboard.press("Control+A");
    await expect.poll(readRange).toEqual(before);
});

test("production forms load and a price edit survives reload", async ({ page }) => {
    await loginAsTestUser(page, "admin");
    await page.goto("/renovation");
    await expect(page.locator("revogr-data .rgCell").first()).toBeVisible({ timeout: 30000 });
    await page.goto("/raw-materials");
    await expect(page.getByRole("tab", { name: "Цены" })).toBeVisible({ timeout: 30000 });
    const pricesLoaded = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "GET",
    );
    await page.getByRole("tab", { name: "Цены" }).click();
    expect((await pricesLoaded).ok()).toBe(true);
    // The first two price columns are pinned into a separate viewport; the
    // editable business-plan values are in the scrollable `rgCol` section.
    const cell = page
        .locator("revogr-viewport-scroll.rgCol revogr-data .rgRow")
        .first()
        .locator(".rgCell")
        .nth(2); // Объём, т
    await expect(cell).toBeVisible();
    const rowId = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const rows = await grid.getVisibleSource("rgRow");
        return String(rows[0].id);
    });
    const response = await page.request.get("/api/raw-materials/prices");
    expect(response.ok()).toBe(true);
    const prices = (await response.json()) as {
        id: string;
        businessPlanQuantityTons: number | null;
    }[];
    const original = String(prices.find((row) => row.id === rowId)!.businessPlanQuantityTons ?? "");
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(original);
    const value = original === "4242" ? "4343" : "4242";
    await expect(page.locator(".form-status__dot--online")).toBeVisible();
    // Move DOM focus off the tab button before sending the grid's Enter.
    await cell.click();
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        const columns = await grid.getColumns();
        const index = columns.findIndex((column) => column.prop === "businessPlanQuantityTons");
        if (index < 0) throw new Error("Missing businessPlanQuantityTons");
        await grid.setCellsFocus({ x: index, y: 0 }, { x: index, y: 0 });
        await grid.refresh();
    });
    // The status spells a single-cell selection as «столбец N, строка M» — the
    // column NUMBER of the form, 1-based («Объём, т» is the 5th). It used to show
    // the column label; the shared selection label now prefers the number, so
    // the assertion follows the current contract.
    await expect(page.locator(".form-status__selection")).toContainText("столбец 5");
    // Open the editor programmatically: a `dblclick` races with the grid's own
    // selection handling (and the focus moved by the click above).
    await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid")!;
        await grid.setCellEdit(0, "businessPlanQuantityTons");
    });
    const editor = page.locator("revogr-edit input, revogr-edit textarea").first();
    await expect(editor).toBeVisible();
    await expect(editor).toBeFocused();
    const saved = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/prices") &&
            response.request().method() === "PATCH",
        { timeout: 15000 },
    );
    await editor.fill(value);
    await expect(editor).toHaveValue(value);
    await editor.press("Enter");
    expect((await saved).ok()).toBe(true);
    const undone = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/undo?") &&
            response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Отменить действие", exact: true }).click();
    expect((await undone).ok()).toBe(true);
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(original);
    const redone = page.waitForResponse(
        (response) =>
            response.url().includes("/api/raw-materials/redo?") &&
            response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Вернуть действие", exact: true }).click();
    expect((await redone).ok()).toBe(true);
    await page.reload();
    await page.getByRole("tab", { name: "Цены" }).click();
    await expect.poll(async () => (await cell.innerText()).replace(/\s/g, "")).toBe(value);
});
