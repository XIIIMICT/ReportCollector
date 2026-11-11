import { afterEach, expect, test } from "bun:test";

import {
    isColumnPinned,
    pinnedColumnsStorageKey,
    readStoredPinnedColumns,
    writeStoredPinnedColumns,
} from "../pinned-columns";

afterEach(() => {
    window.localStorage.clear();
});

test("column pin overrides survive a reload and stay per table", () => {
    writeStoredPinnedColumns("renovation", "plan", { col_004: true, col_005: false });
    expect(readStoredPinnedColumns("renovation", "plan")).toEqual({
        col_004: true,
        col_005: false,
    });
    expect(readStoredPinnedColumns("renovation", "assets")).toEqual({});
    expect(window.localStorage.getItem(pinnedColumnsStorageKey("renovation", "plan"))).toBe(
        JSON.stringify({ col_004: true, col_005: false }),
    );
});

test("an empty override set removes the storage entry", () => {
    writeStoredPinnedColumns("kppk", "products", { rawMaterialName: false });
    writeStoredPinnedColumns("kppk", "products", {});
    expect(window.localStorage.getItem(pinnedColumnsStorageKey("kppk", "products"))).toBeNull();
});

test("a malformed stored entry pins nothing", () => {
    window.localStorage.setItem(pinnedColumnsStorageKey("raw-materials", "plan"), "{not json");
    expect(readStoredPinnedColumns("raw-materials", "plan")).toEqual({});
});

test("the user's override wins over the descriptor's default", () => {
    // `pinned` on the descriptor is the DEFAULT (identity columns ship pinned);
    // an explicit `false` is the user unpinning it, and `true` pins a regular one.
    expect(isColumnPinned("rawMaterialName", {}, true)).toBe(true);
    expect(isColumnPinned("rawMaterialName", { rawMaterialName: false }, true)).toBe(false);
    expect(isColumnPinned("demandPlanTons", { demandPlanTons: true }, false)).toBe(true);
    expect(isColumnPinned("demandPlanTons", {}, false)).toBe(false);
});
