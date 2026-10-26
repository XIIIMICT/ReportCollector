import { afterEach, expect, test } from "bun:test";

import {
    columnSortingStorageKey,
    readStoredSorting,
    writeStoredSorting,
} from "../use-grid-column-filters";

afterEach(() => {
    if (!globalThis.window) return;
    for (const key of Object.keys(window.localStorage)) {
        if (key.startsWith("rc:sorting:")) window.localStorage.removeItem(key);
    }
});

test("a sorting round-trips through storage", () => {
    writeStoredSorting("renovation", "assets", [
        { prop: "name", order: "desc" },
        { prop: "code", order: "asc" },
    ]);
    expect(readStoredSorting("renovation", "assets")).toEqual([
        { prop: "name", order: "desc" },
        { prop: "code", order: "asc" },
    ]);
});

test("an empty sorting removes the entry instead of writing []", () => {
    writeStoredSorting("renovation", "assets", [{ prop: "name", order: "asc" }]);
    writeStoredSorting("renovation", "assets", []);
    expect(window.localStorage.getItem(columnSortingStorageKey("renovation", "assets"))).toBeNull();
    expect(readStoredSorting("renovation", "assets")).toEqual([]);
});

test("a malformed entry is dropped, never thrown", () => {
    const key = columnSortingStorageKey("renovation", "assets");
    window.localStorage.setItem(key, "{ not json");
    expect(readStoredSorting("renovation", "assets")).toEqual([]);

    window.localStorage.setItem(
        key,
        JSON.stringify([
            { prop: "name", order: "sideways" },
            { order: "asc" },
            { prop: "ok", order: "asc" },
        ]),
    );
    expect(readStoredSorting("renovation", "assets")).toEqual([{ prop: "ok", order: "asc" }]);
});

test("one project's sorting never reaches another", () => {
    writeStoredSorting("renovation", "assets", [{ prop: "name", order: "asc" }]);
    expect(readStoredSorting("kppk", "assets")).toEqual([]);
});
