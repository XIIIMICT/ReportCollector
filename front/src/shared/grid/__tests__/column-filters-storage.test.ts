import { afterEach, expect, test } from "bun:test";

import { columnFiltersStorageKey } from "../use-grid-column-filters";

const original = globalThis.window?.localStorage;

afterEach(() => {
    if (globalThis.window && original) {
        for (const key of Object.keys(window.localStorage)) {
            if (key.startsWith("rc:filters:")) window.localStorage.removeItem(key);
        }
    }
});

test("each project and section gets its own storage key", () => {
    // The URL used to carry every table's filters under ONE key, which let a
    // «Предприятия» filter reach the plan and fail its load. The key now names
    // the project and the section, so no two tables can collide.
    expect(columnFiltersStorageKey("renovation", "renovationAssets")).toBe(
        "rc:filters:renovation:renovationAssets",
    );
    expect(columnFiltersStorageKey("kppk", "realization")).not.toBe(
        columnFiltersStorageKey("renovation", "realization"),
    );
});

test("a stored filter round-trips, and a broken entry is dropped", () => {
    const key = columnFiltersStorageKey("renovation", "renovationAssets");
    window.localStorage.setItem(
        key,
        JSON.stringify({ name: { column: "name", values: ["АО «БСЗ»"], includeNulls: false } }),
    );
    const raw = window.localStorage.getItem(key);
    expect(raw).toContain("АО «БСЗ»");

    window.localStorage.setItem(key, "{ not json");
    expect(() => JSON.parse(window.localStorage.getItem(key) as string)).toThrow();
});
