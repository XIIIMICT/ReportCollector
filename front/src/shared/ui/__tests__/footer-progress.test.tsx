import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { FooterProgress } from "../footer-progress";

afterEach(cleanup);

test("an unsized operation shows only motion", () => {
    const view = render(<FooterProgress label="Идёт загрузка в базу..." progress={0} total={0} />);
    const bar = view.getByRole("progressbar");
    // React Aria renders `aria-valuenow` only for a determinate bar.
    expect(bar.hasAttribute("aria-valuenow")).toBe(false);
    expect(bar.getAttribute("aria-label")).toBe("Идёт загрузка в базу...");
    expect(bar.closest(".table-footer__progress")).toBeTruthy();
});

test("a sized operation shows the server's units, capped at the total", () => {
    const view = render(<FooterProgress label="Запись строк..." progress={30} total={80} />);
    const bar = view.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("30");
    expect(bar.getAttribute("aria-valuemax")).toBe("80");
    expect(bar.getAttribute("aria-label")).toBe("Запись строк...");

    // A stale poll never pushes the bar past its total.
    view.rerender(<FooterProgress label="Запись строк..." progress={120} total={80} />);
    expect(view.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("80");
});
