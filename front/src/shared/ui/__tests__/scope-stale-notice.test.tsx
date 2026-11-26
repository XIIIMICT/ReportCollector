import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { ScopeStaleNotice } from "../workspace-feedback";

afterEach(cleanup);

test("the notice is an announced status with the caller's message", () => {
    const view = render(
        <ScopeStaleNotice>
            Загружается выбранный срез. Редактирование временно недоступно.
        </ScopeStaleNotice>,
    );
    const notice = view.getByRole("status");
    expect(notice.textContent).toBe(
        "Загружается выбранный срез. Редактирование временно недоступно.",
    );
    // The banner floats above the grid; its geometry is style, not layout.
    expect(notice.style.position).toBe("absolute");
    expect(notice.style.zIndex).toBe("4");
    expect(notice.style.inset).toBe("0.5rem 0.75rem auto");
});

test("an empty notice still renders the status slot", () => {
    const view = render(<ScopeStaleNotice>{""}</ScopeStaleNotice>);
    expect(view.getByRole("status").textContent).toBe("");
});
