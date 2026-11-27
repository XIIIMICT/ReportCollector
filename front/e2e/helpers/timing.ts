import { expect, type Page, type Request } from "@playwright/test";

/** HoverDropdown's pointer-grace / Escape-suppression window (docs/GRID.md). */
export const HOVER_GRACE_MS = 250;
/**
 * Invalid pastes are rejected synchronously; this window only covers the React
 * state flush after the warning toast, not a server debounce.
 */
export const PASTE_SETTLE_MS = 300;
/** A rejected cell edit is decided locally; this only covers its state flush. */
export const EDIT_REJECT_SETTLE_MS = 300;
/** A grid data refresh can remount cells while a draft row is being targeted. */
export const GRID_SETTLE_MS = 300;

const GRACE_MARGIN_MS = 100;
const SAMPLE_MS = 25;

/**
 * Re-check an expectation across a named client-side timer window.
 *
 * The wait itself proves nothing — the repeated assertions do. This is for
 * "nothing reopened / no request was sent / the editor never mounted" contracts
 * that have no DOM event marking their end. Do not use it as a substitute for a
 * web-first assertion that can observe the target state directly.
 */
export async function expectStableForGrace(
    page: Page,
    expectation: () => void | Promise<void>,
    durationMs: number,
): Promise<void> {
    const deadline = Date.now() + durationMs + GRACE_MARGIN_MS;
    for (;;) {
        await expectation();
        if (Date.now() >= deadline) return;
        await page.waitForTimeout(SAMPLE_MS);
    }
}

export async function expectHiddenForGrace(
    page: Page,
    locator: import("@playwright/test").Locator,
    durationMs = HOVER_GRACE_MS,
): Promise<void> {
    await expectStableForGrace(page, () => expect(locator).toHaveCount(0), durationMs);
}

export async function expectVisibleForGrace(
    page: Page,
    locator: import("@playwright/test").Locator,
    durationMs = HOVER_GRACE_MS,
): Promise<void> {
    await expectStableForGrace(page, () => expect(locator).toBeVisible(), durationMs);
}

/** Assert a request predicate's counter stays put across a named grace window. */
export async function expectNoRequestsForGrace(
    page: Page,
    requests: Request[],
    predicate: (request: Request) => boolean,
    durationMs: number,
): Promise<void> {
    const before = requests.filter(predicate).length;
    await expectStableForGrace(
        page,
        () => {
            expect(requests.filter(predicate).length).toBe(before);
        },
        durationMs,
    );
}
