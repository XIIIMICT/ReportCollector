import { afterEach, expect, test } from "bun:test";

import { act } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";

import {
    readToolbarFilters,
    toolbarFilterParam,
    useToolbarFilters,
} from "@/shared/ui/use-toolbar-filters";

afterEach(() => {
    window.location.href = "http://localhost:5000/kppk";
});

test("panel filters of different tabs live side by side and stay apart", () => {
    window.location.href =
        "http://localhost:5000/kppk?section=a&f.a.org=U1&f.b.org=U2&f.a.period=2026-01";
    // Each tab reads only its own parameters — a shared name never crosses over.
    expect(readToolbarFilters("a")).toEqual({ org: "U1", period: "2026-01" });
    expect(readToolbarFilters("b")).toEqual({ org: "U2" });
    expect(readToolbarFilters("c")).toEqual({});
    expect(readToolbarFilters(null)).toEqual({});
});

test("a multi-select filter round-trips as a JSON list", () => {
    window.location.href = "http://localhost:5000/renovation?section=plan";
    const set = renderAndSet();
    act(() => set("slice", ["bp", "op"]));
    const url = new URL(window.location.href);
    expect(url.searchParams.get(toolbarFilterParam("plan", "slice"))).toBe('["bp","op"]');
    expect(readToolbarFilters("plan")["slice"]).toEqual(["bp", "op"]);
    // An empty list means "no constraint": the parameter disappears.
    act(() => set("slice", []));
    expect(window.location.search).not.toContain("slice");
});

test("a cleared filter is dropped from the URL, not stored as empty", () => {
    window.location.href = "http://localhost:5000/kppk?section=kppkRealization";
    const set = renderAndSet();
    act(() => set("org", "U1"));
    expect(window.location.search).toContain("f.kppkRealization.org=U1");
    act(() => set("org", null));
    expect(window.location.search).not.toContain("org");
    expect(readToolbarFilters("kppkRealization")).toEqual({});
});

/** Mount the hook on a tiny page and expose its setter. */
function renderAndSet() {
    let setter: ((name: string, value: string | string[] | null) => void) | null = null;
    const tabId = new URL(window.location.href).searchParams.get("section") ?? "plan";
    function Harness() {
        const { setFilter } = useToolbarFilters(tabId);
        setter = (name, value) => setFilter(tabId, name, value);
        return <span />;
    }
    const container = document.createElement("div");
    document.body.append(container);
    container.innerHTML = renderToString(<Harness />);
    return (name: string, value: string | string[] | null) => {
        if (!setter) throw new Error("the hook is not mounted");
        setter(name, value);
    };
}

test("SSR renders no filters and restores the URL set after hydration", async () => {
    window.location.href =
        "http://localhost:5000/kppk?section=kppkRealization&f.kppkRealization.year=2027";
    let seen: Record<string, unknown> = { marker: true };
    function Harness() {
        const { filters } = useToolbarFilters("kppkRealization");
        seen = filters;
        return <span>{String(filters.year ?? "—")}</span>;
    }
    const container = document.createElement("div");
    document.body.append(container);
    const html = renderToString(<Harness />);
    expect(html).toContain("—");
    container.innerHTML = html;
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
        await act(async () => {
            root = hydrateRoot(container, <Harness />);
        });
        expect(container.textContent).toContain("2027");
        expect(seen).toEqual({ year: "2027" });
    } finally {
        await act(async () => root?.unmount());
        container.remove();
    }
});
