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
    // The stage is named, but there is no percentage to state yet.
    expect(view.getByText("Идёт загрузка в базу...")).toBeTruthy();
    expect(bar.querySelector(".progress-bar__output")).toBeNull();
});

test("a sized operation shows the stage and its percentage", () => {
    const view = render(<FooterProgress label="Запись строк..." progress={30} total={80} />);
    const bar = view.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("30");
    expect(bar.getAttribute("aria-valuemax")).toBe("80");
    expect(bar.getAttribute("aria-label")).toBe("Запись строк...");

    // The label carries the current stage, the output the share done.
    expect(view.getByText("Запись строк...")).toBeTruthy();
    expect(bar.querySelector(".progress-bar__output")?.textContent?.trim()).toMatch(/%$/);

    // A stale poll never pushes the bar past its total.
    view.rerender(<FooterProgress label="Запись строк..." progress={120} total={80} />);
    expect(view.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("80");
    expect(view.getByRole("progressbar").querySelector(".progress-bar__output")?.textContent).toBe(
        "100%",
    );
});
