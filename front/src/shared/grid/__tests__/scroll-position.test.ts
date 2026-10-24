import { afterEach, beforeEach, expect, test } from "bun:test";

import {
    clearScrollPosition,
    readScrollPosition,
    scrollScopeKey,
    writeScrollPosition,
} from "../scroll-position";

beforeEach(() => {
    window.localStorage.clear();
});

afterEach(() => {
    window.localStorage.clear();
});

test("a missing entry reads as no position", () => {
    expect(readScrollPosition("renovation:plan")).toBeNull();
});

test("a written position comes back by form and scope", () => {
    writeScrollPosition("renovation:plan", { rowId: "row-42", scope: "" });
    expect(readScrollPosition("renovation:plan")).toEqual({ rowId: "row-42", scope: "" });
    // Another form never inherits it.
    expect(readScrollPosition("raw_materials:assets")).toBeNull();
});

test("the same form keeps a separate position per scope", () => {
    // Clearing a filter must return to where the user was BEFORE filtering, so
    // the unfiltered scope and the filtered one cannot share an entry.
    writeScrollPosition("kppk:kppkPositions", { rowId: "plain", scope: "" });
    writeScrollPosition("kppk:kppkPositions", {
        rowId: "filtered",
        scope: scrollScopeKey({ filters: [{ column: "col_002", values: ["A"] }] }),
    });
    expect(readScrollPosition("kppk:kppkPositions")?.rowId).toBe("plain");
    expect(
        readScrollPosition(
            "kppk:kppkPositions",
            scrollScopeKey({ filters: [{ column: "col_002", values: ["A"] }] }),
        )?.rowId,
    ).toBe("filtered");
});

test("a malformed value is ignored instead of throwing", () => {
    window.localStorage.setItem("reportcollector:scroll-position:x:", "{not json");
    expect(readScrollPosition("x")).toBeNull();
    window.localStorage.setItem("reportcollector:scroll-position:y:", '{"rowId": 42}');
    expect(readScrollPosition("y")).toBeNull();
    window.localStorage.setItem("reportcollector:scroll-position:z:", '{"rowId": ""}');
    expect(readScrollPosition("z")).toBeNull();
});

test("clearing removes one scope, or every scope of a form", () => {
    writeScrollPosition("form:a", { rowId: "1", scope: "" });
    writeScrollPosition("form:a", { rowId: "2", scope: "f" });
    writeScrollPosition("form:b", { rowId: "3", scope: "" });

    clearScrollPosition("form:a", "");
    expect(readScrollPosition("form:a")).toBeNull();
    expect(readScrollPosition("form:a", "f")?.rowId).toBe("2");

    clearScrollPosition("form:a");
    expect(readScrollPosition("form:a", "f")).toBeNull();
    // A neighbouring form is untouched.
    expect(readScrollPosition("form:b")?.rowId).toBe("3");
});

test("scrollScopeKey is stable for equal descriptions and empty for nothing", () => {
    expect(scrollScopeKey(null)).toBe("");
    expect(scrollScopeKey(undefined)).toBe("");
    expect(scrollScopeKey("abc")).toBe("abc");
    expect(scrollScopeKey({ a: 1, b: 2 })).toBe(scrollScopeKey({ a: 1, b: 2 }));
});
