import { expect, test } from "bun:test";

import { act } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";

import { readWorkspaceQuery } from "@/shared/ui/query-state";
import { useViewState } from "@/shared/ui/use-view-state";

import { useRestoredState } from "../use-restored-state";

test("SSR/hydration match before restoring query filters and stored table view", async () => {
    const originalUrl = window.location.href;
    const storageKey = "reportcollector:view:hydration-test";
    const dimensionsKey = "reportcollector:table-dimensions:hydration-test";
    window.location.href = "http://localhost:5000/kppk?month=1&year=2026";
    window.localStorage.setItem(
        storageKey,
        JSON.stringify({ headerPinned: false, showServiceColumns: true }),
    );
    window.localStorage.setItem(dimensionsKey, JSON.stringify({ rowHeight: 18 }));
    const errors: unknown[] = [];
    let restoreCalls = 0;
    function Harness() {
        const [month, setMonth] = useRestoredState<string | null>(null, () => {
            restoreCalls++;
            return readWorkspaceQuery("month");
        });
        const view = useViewState("hydration-test");
        return (
            <div
                data-pinned={view.headerPinned}
                data-service={view.showServiceColumns}
                data-height={view.rowHeight}
            >
                <span>{month === "1" ? "январь" : "Месяц"}</span>
                <button onClick={() => setMonth("2")}>Изменить</button>
            </div>
        );
    }
    const container = document.createElement("div");
    document.body.append(container);
    const html = renderToString(<Harness />);
    expect(restoreCalls).toBe(0);
    expect(html).toContain("Месяц");
    expect(html).toContain('data-height="36"');
    container.innerHTML = html;
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
        await act(async () => {
            root = hydrateRoot(container, <Harness />, {
                onRecoverableError: (error) => errors.push(error),
            });
        });
        expect(errors).toEqual([]);
        expect(container.textContent).toContain("январь");
        expect(container.firstElementChild?.getAttribute("data-height")).toBe("18");
        expect(container.firstElementChild?.getAttribute("data-pinned")).toBe("false");
        expect(container.firstElementChild?.getAttribute("data-service")).toBe("true");
        await act(async () => {
            container.querySelector("button")?.click();
        });
        expect(container.textContent).toContain("Месяц");
    } finally {
        await act(async () => root?.unmount());
        container.remove();
        window.location.href = originalUrl;
        window.localStorage.removeItem(storageKey);
        window.localStorage.removeItem(dimensionsKey);
    }
});
