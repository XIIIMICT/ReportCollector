import { expect, test, type Locator, type Page } from "@playwright/test";

import { loginAsLocalUser } from "./helpers/auth";

test.beforeEach(async ({ page }, info) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    if (info.project.name === "ui-keycloak") {
        const username = process.env.E2E_KEYCLOAK_USERNAME;
        const password = process.env.E2E_KEYCLOAK_PASSWORD;
        if (!username || !password)
            throw new Error("Set E2E_KEYCLOAK_USERNAME and E2E_KEYCLOAK_PASSWORD");
        await page.goto("/api/auth/login");
        await page.locator('input[name="username"]').fill(username);
        await page.locator('input[name="password"]').fill(password);
        await page.locator('input[type="submit"], button[type="submit"]').click();
        await expect(page).toHaveURL(/:5000\/(?:renovation|raw-materials|kppk)?$/);
    } else {
        await loginAsLocalUser(page, {
            email: "evtushenko.sn@ruschem.ru",
            password: "TestPassword123",
        });
    }
});

async function openTable(page: Page, url: string) {
    await page.goto(url);
    await expect(page.locator("revo-grid")).toBeVisible({ timeout: 40000 });
    await expect(page.locator("revogr-data .rgCell:not(.report-row-header)").first()).toBeVisible();
}

test("plan export persists its scope and downloads a completed workbook", async ({ page }) => {
    test.setTimeout(90000);
    await openTable(page, "/renovation");
    const origin = new URL(page.url()).origin;
    const rowsResponse = await page.request.get("/api/renovation/plan/rows?offset=0&limit=1");
    expect(rowsResponse.ok()).toBe(true);
    const rows = await rowsResponse.json();
    expect(rows.rows.length).toBeGreaterThan(0);
    const request = { rowIds: [rows.rows[0].id] };
    const created = await page.request.post("/api/renovation/plan/export/jobs", {
        data: request,
        headers: { Origin: origin },
    });
    expect(created.status()).toBe(202);
    const job = await created.json();
    await page.reload();
    await expect
        .poll(
            async () => {
                const response = await page.request.get(
                    `/api/renovation/plan/export/jobs/${job.id}`,
                );
                const status = await response.json();
                expect(status.error).toBeNull();
                return status.status;
            },
            { timeout: 60000 },
        )
        .toBe("done");
    const file = await page.request.get(`/api/renovation/plan/export/jobs/${job.id}/file`);
    expect(file.ok()).toBe(true);
    expect((await file.body()).subarray(0, 2).toString()).toBe("PK");
    const repeated = await page.request.post("/api/renovation/plan/export/jobs", {
        data: request,
        headers: { Origin: origin },
    });
    expect((await repeated.json()).id).toBe(job.id);
});

async function viewAction(page: Page, name: string) {
    await page
        .getByRole("button", { name: "Вид таблицы", exact: true })
        .filter({ visible: true })
        .click();
    await page.getByRole("menuitem", { name, exact: true }).click();
    await page.mouse.move(800, 900);
}

test("invalid plan import reports its durable result after reload", async ({ page }) => {
    test.setTimeout(90000);
    await openTable(page, "/renovation");
    const origin = new URL(page.url()).origin;
    const before = await page.request.get("/api/renovation/plan/rows?offset=0&limit=1");
    const rows = await before.json();
    const accepted = await page.request.post("/api/renovation/plan/import/apply", {
        headers: { Origin: origin },
        multipart: {
            file: {
                name: "invalid.xlsb",
                mimeType: "application/octet-stream",
                buffer: Buffer.from("invalid workbook"),
            },
        },
    });
    expect(accepted.status()).toBe(202);
    const job = await accepted.json();
    await page.reload();
    await expect
        .poll(
            async () => {
                const response = await page.request.get(
                    `/api/renovation/plan/import/jobs/${job.id}`,
                );
                expect(response.ok()).toBe(true);
                return (await response.json()).status;
            },
            { timeout: 60000 },
        )
        .toBe("failed");
    const status = await page.request.get(`/api/renovation/plan/import/jobs/${job.id}`);
    const result = await status.json();
    expect(result.error).toBeTruthy();
    expect(result.new_rows).toBe(0);
    expect(result.updated_rows).toBe(0);
    const after = await page.request.get("/api/renovation/plan/rows?offset=0&limit=1");
    expect((await after.json()).rows).toEqual(rows.rows);
});

test("KPPK restores sorting without merges and uses the same toolbar row gap", async ({ page }) => {
    await openTable(page, "/kppk");
    await page.getByRole("tab", { name: "Реализация", exact: true }).click();
    const grid = page.locator("revo-grid");
    await expect(grid).toBeVisible();
    const mergeCount = () =>
        grid.evaluate(
            (element) =>
                ((element as HTMLElement & { cellMerge?: unknown[] }).cellMerge ?? []).length,
        );
    await expect.poll(mergeCount).toBeGreaterThan(0);
    const toolbar = page.locator(".workspace-card .table-toolbar--stacked");
    const gaps = await toolbar.evaluate((element) => {
        const row = element.querySelector(".table-toolbar__row")!;
        const filters = element.querySelector(".toolbar-filters__row")!;
        const controls = row.querySelector(".table-toolbar__controls")!;
        return {
            vertical: filters.getBoundingClientRect().top - row.getBoundingClientRect().bottom,
            horizontal: parseFloat(getComputedStyle(controls).marginRight),
        };
    });
    expect(Math.abs(gaps.vertical - gaps.horizontal)).toBeLessThan(1);
    for (const width of [900, 600, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await expect
            .poll(() =>
                toolbar.evaluate((element) => {
                    const row = element.querySelector(".table-toolbar__row")!;
                    const filters = element.querySelector(".toolbar-filters__row")!;
                    return filters.getBoundingClientRect().top - row.getBoundingClientRect().bottom;
                }),
            )
            .toBeGreaterThan(11);
    }
    await page.setViewportSize({ width: 1600, height: 1000 });
    const header = grid
        .locator(".rgHeaderCell")
        .filter({ has: page.locator('[data-prop="product"]') });
    await header.click();
    await expect(grid).toHaveClass(/data-grid--user-sorted/);
    await expect.poll(mergeCount).toBe(0);
    await page.reload();
    await expect(grid).toHaveClass(/data-grid--user-sorted/);
    await expect.poll(mergeCount).toBe(0);
    await viewAction(page, "Сбросить все фильтры и сортировки");
    await expect.poll(mergeCount).toBeGreaterThan(0);
});

test("plan header filter priorities persist and facets use only earlier filters", async ({
    page,
}) => {
    await openTable(page, "/renovation");
    const rowsResponse = await page.request.get("/api/renovation/plan/rows?limit=20");
    const body = await rowsResponse.json();
    const row = body.rows.find((item: { values: Record<string, unknown> }) => item.values.col_011);
    expect(row).toBeTruthy();
    const filters = {
        col_011: { column: "col_011", values: [row.values.col_011], includeNulls: false },
        __assetName: { column: "__assetName", values: [row.assetName], includeNulls: false },
    };
    await page.evaluate(
        (value) => localStorage.setItem("rc:filters:renovation:plan", JSON.stringify(value)),
        filters,
    );
    await page.reload();
    const first = page.locator(
        '.renovation-grid__header-cell .header-filter-button[data-filter-order="1"]',
    );
    const second = page.locator(
        '.renovation-grid__header-cell .header-filter-button[data-filter-order="2"]',
    );
    await expect(first).toHaveText("1");
    await expect(second).toHaveText("2");
    expect(
        await first.evaluate((button) => {
            const order = button
                .querySelector(".header-filter-button__order")!
                .getBoundingClientRect();
            const icon = button
                .querySelector(".header-filter-button__icon")!
                .getBoundingClientRect();
            return order.right <= icon.left;
        }),
    ).toBe(true);
    const status = page.getByRole("button", { name: "Статус таблицы" });
    await status.focus();
    await expect(page.locator(".form-status-tooltip")).toBeVisible();
    const filterList = page
        .locator(".form-status-detail__filters")
        .filter({ hasText: "Фильтры:" })
        .locator("ol");
    await expect(filterList.locator("li").last()).toHaveText("Предприятие");
    await expect(filterList.locator("li")).toHaveCount(2);
    await page.mouse.move(800, 400);
    const request = page.waitForRequest(
        (request) =>
            request.url().includes("/plan/facets/query") &&
            new URL(request.url()).searchParams.get("column") === "__assetName",
    );
    await second.evaluate((element) => (element as HTMLButtonElement).click());
    const sent = await request;
    expect(sent.method()).toBe("POST");
    expect(new URL(sent.url()).searchParams.has("filters")).toBe(false);
    expect(sent.postDataJSON().filters).toEqual([filters.col_011]);
    await expect(page.locator(".column-filter__popover")).toBeVisible();
    await page.keyboard.press("Escape");
    const firstRequest = page.waitForRequest(
        (request) =>
            request.url().includes("/plan/facets/query") &&
            new URL(request.url()).searchParams.get("column") === "col_011",
    );
    await first.evaluate((element) => (element as HTMLButtonElement).click());
    expect((await firstRequest).postDataJSON().filters).toEqual([]);
    await page.keyboard.press("Escape");
    await viewAction(page, "Сбросить все фильтры и сортировки");
});

async function height(node: Locator) {
    return node.evaluate((element) => element.getBoundingClientRect().height);
}

async function expectFunnelStable(page: Page, cell: Locator, button: Locator) {
    const box = (await cell.boundingBox())!;
    await page.mouse.move(box.x + box.width - 12, box.y + box.height + 6);
    await page.mouse.move(box.x + box.width - 12, box.y + box.height - 2, { steps: 8 });
    await expect(button).toHaveCSS("opacity", "1");
    const samples = await button.evaluate(async (element) => {
        const values: number[] = [];
        for (let index = 0; index < 30; index++) {
            await new Promise(requestAnimationFrame);
            values.push(Number(getComputedStyle(element).opacity));
        }
        return values;
    });
    expect(Math.min(...samples)).toBe(1);
    await button.click();
    await expect(page.locator(".column-filter__popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".column-filter__popover")).toHaveCount(0);
}

test("toolbar follows visible controls and header funnel stays open at the bottom edge", async ({
    page,
}) => {
    const pressResponderWarnings: string[] = [];
    page.on("console", (message) => {
        if (message.type() === "warning" && message.text().includes("PressResponder")) {
            pressResponderWarnings.push(message.text());
        }
    });
    await openTable(page, "/raw-materials");
    const toolbar = page.locator(".workspace-card .table-toolbar");
    const archive = toolbar.locator(".archive-autocomplete");
    await expect(archive).not.toHaveClass(/--hidden/);
    const tall = await height(toolbar);
    await page.getByRole("tab", { name: "Цены", exact: true }).click();
    await expect(archive).toHaveClass(/--hidden/);
    await expect.poll(() => height(toolbar)).toBeLessThan(tall);
    const panel = toolbar.locator("..").locator("..");
    await expect
        .poll(async () => Math.abs((await height(panel)) - (await height(toolbar))))
        .toBeLessThan(1);
    const button = page.locator('.report-header-funnels [data-prop="rawMaterialName"]');
    const cell = page
        .locator(".rgHeaderCell")
        .filter({ has: page.locator('[data-prop="rawMaterialName"]') });
    await expectFunnelStable(page, cell, button);
    await cell.click();
    await expect(cell.locator(".sort-indicator")).toHaveCSS("opacity", "1");
    expect(pressResponderWarnings).toEqual([]);
});

test("sorted header text does not move when its active funnel is hovered", async ({ page }) => {
    await openTable(page, "/raw-materials");

    const value = await page.evaluate(async () => {
        const grid = document.querySelector("revo-grid") as
            | (HTMLElement & { getSource?: () => Promise<Array<Record<string, unknown>>> })
            | null;
        const source = (await grid?.getSource?.()) ?? [];
        return (
            source.find((row) => typeof row.rawMaterialName === "string")?.rawMaterialName ?? null
        );
    });
    expect(value).toBeTruthy();
    await page.evaluate((filterValue) => {
        localStorage.setItem(
            "rc:filters:raw-materials:plan",
            JSON.stringify({
                rawMaterialName: {
                    column: "rawMaterialName",
                    values: [filterValue],
                    includeNulls: false,
                },
            }),
        );
    }, value);
    await page.reload();
    await expect(page.locator("revo-grid")).toBeVisible({ timeout: 40000 });

    const cell = page
        .locator(".rgHeaderCell")
        .filter({ has: page.locator('[data-prop="rawMaterialName"]') });
    const title = cell.locator(".report-header__title");
    const funnel = page.locator('.report-header-funnels [data-prop="rawMaterialName"]');
    await expect(funnel).toHaveClass(/is-active/);

    // Sort the already-filtered column, then move away so the measurement is
    // taken before hover can reveal anything new.
    if (
        !(await cell.evaluate(
            (element) => element.classList.contains("asc") || element.classList.contains("desc"),
        ))
    ) {
        await title.click();
    }
    await expect(cell).toHaveClass(/asc|desc/);
    await page.mouse.move(10, 10);
    await page.waitForTimeout(350);
    const before = await title.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const rect = range.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
    });

    await title.hover({ force: true });
    await page.waitForTimeout(350);
    const after = await title.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const rect = range.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
    });
    expect(after.left).toBeCloseTo(before.left, 1);
    expect(after.right).toBeCloseTo(before.right, 1);
    expect(after.width).toBeCloseTo(before.width, 1);
});

test("timestamp filter keeps its bounds and result count after reload, reset persists", async ({
    page,
}) => {
    await openTable(page, "/renovation");
    await viewAction(page, "Показать данные о редактировании строк");
    await page.locator(".revo-grid-external-scrollbar").evaluate((element) => {
        element.scrollLeft = element.scrollWidth;
    });
    const button = page.getByRole("button", { name: "Фильтр: Время добавления", exact: true });
    await button.locator("..").hover({ force: true });
    await button.evaluate((element) => (element as HTMLButtonElement).click());
    const minimum = page.locator(".column-filter__popover .date-time-picker").first();
    await minimum.locator("button").click();
    const time = page.locator(".date-time-picker__popover .date-time-picker__time").first();
    await expect(page.locator(".date-time-picker__popover")).toBeVisible();
    const calendarDay = page.getByRole("button", { name: /среда, 14 октября 2026 г\./ }).last();
    await expect(calendarDay).toBeVisible();
    await calendarDay.click();
    await expect(minimum.locator('[data-type="day"]')).toHaveText("14");
    await expect(time).toBeVisible();
    const beforeRequest = page.waitForResponse(
        (response) =>
            response.url().includes("/plan/rows/query?") &&
            (response.request().postData() ?? "").includes("createdAt") &&
            response.status() === 200,
    );
    await page.getByRole("button", { name: "Применить", exact: true }).click();
    const before = await beforeRequest;
    expect(new URL(before.url()).searchParams.has("filters")).toBe(false);
    const beforeQuery = before.request().postDataJSON().filters;
    const beforeBody = await before.json();
    expect(
        beforeQuery.find((filter: { column: string }) => filter.column === "createdAt").from,
    ).toContain("2026-10-13T21:00:00");
    const afterRequest = page
        .waitForResponse(
            (response) =>
                response.url().includes("/plan/rows/query?") &&
                (response.request().postData() ?? "").includes("createdAt") &&
                response.status() === 200,
        )
        .then((response) => response.request().postDataJSON().filters);
    await page.reload();
    expect(await afterRequest).toEqual(beforeQuery);
    await expect(page.getByText(new RegExp(`Строки: ${beforeBody.total} найдено`))).toBeVisible();
    expect(beforeBody.total).toBeGreaterThan(0);
    await viewAction(page, "Сбросить все фильтры и сортировки");
    expect(
        await page.evaluate(() => localStorage.getItem("rc:filters:renovation:plan")),
    ).toBeNull();
});

test("Excel actions share a group and fullscreen Escape closes filter, selection, then fullscreen", async ({
    page,
}) => {
    await openTable(page, "/renovation");
    const download = page.getByRole("button", { name: "Скачать в Excel", exact: true });
    const group = download.locator("..");
    await expect(group).toHaveAttribute("data-slot", "button-group");
    await expect(
        group.getByRole("button", { name: "Загрузить из Excel", exact: true }),
    ).toBeVisible();
    await download.click();
    await expect(page.getByRole("menuitem", { name: "Скачать видимую таблицу" })).toBeVisible();
    await page.keyboard.press("Escape");
    await viewAction(page, "В полный экран");
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    const cell = page
        .locator(
            "revogr-data .rgCell:not(.report-row-header):not(.renovation-grid__summary-cell):not(.disabled):visible",
        )
        .first();
    await cell.click({ force: true });
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(1);
    const filter = page
        .locator(".renovation-grid__header-cell")
        .filter({ has: page.locator(".header-filter-button") })
        .first();
    await filter.hover();
    await filter.locator("button").click();
    await expect(page.locator(".column-filter__popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".column-filter__popover")).toHaveCount(0);
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(page.locator(".fullscreen-panel .form-status__selection--shown")).toHaveCount(0);
    await expect(page.locator(".app-shell")).toHaveClass(/app-shell--fullscreen/);
    await page.keyboard.press("Escape");
    await expect(page.locator(".app-shell")).not.toHaveClass(/app-shell--fullscreen/);
});
