import { afterEach, expect, test } from "bun:test";

import {
    hiddenColumnBlocks,
    hiddenColumnsStorageKey,
    hideColumns,
    readStoredHiddenColumns,
    showColumns,
    writeStoredHiddenColumns,
    type HiddenColumnAnchors,
} from "../hidden-columns";

afterEach(() => {
    window.localStorage.clear();
});

test("adjacent hidden columns form ONE block and reveal together", () => {
    const hidden: HiddenColumnAnchors = { b: 1, c: 1, e: 0 };
    expect(hiddenColumnBlocks(["a", "b", "c", "d", "e", "f"], hidden)).toEqual([
        { props: ["b", "c"], anchor: 1 },
        { props: ["e"], anchor: 0 },
    ]);
});

test("hiding an adjacent column at a higher level lifts the block anchor", () => {
    const first = hideColumns({}, ["b"], 1);
    const second = hideColumns(first, ["c"], 0);
    expect(hiddenColumnBlocks(["a", "b", "c", "d"], second)).toEqual([
        { props: ["b", "c"], anchor: 0 },
    ]);
});

test("revealing a block removes exactly its columns", () => {
    const hidden = hideColumns({}, ["b", "c", "e"], 1);
    const next = showColumns(hidden, ["b", "c"]);
    expect(next).toEqual({ e: 1 });
    expect(hiddenColumnBlocks(["a", "b", "c", "d", "e"], next)).toEqual([
        { props: ["e"], anchor: 1 },
    ]);
});

test("a hidden column anchored at a higher header level keeps that level", () => {
    const hidden = hideColumns({}, ["demandPlanTons"], 0);
    expect(hiddenColumnBlocks(["demandPlanTons", "consumptionFactTons"], hidden)).toEqual([
        { props: ["demandPlanTons"], anchor: 0 },
    ]);
});

test("merging a leaf-hidden column with a higher-level block uses the higher level", () => {
    const hidden: HiddenColumnAnchors = { groupLeft: 0, leafRight: 5 };
    expect(hiddenColumnBlocks(["groupLeft", "leafRight"], hidden)).toEqual([
        { props: ["groupLeft", "leafRight"], anchor: 0 },
    ]);
});

test("the table's hidden columns survive a reload and stay per table", () => {
    writeStoredHiddenColumns("raw-materials", "plan", { demandPlanTons: 1 });
    expect(readStoredHiddenColumns("raw-materials", "plan")).toEqual({ demandPlanTons: 1 });
    expect(readStoredHiddenColumns("raw-materials", "prices")).toEqual({});
    expect(window.localStorage.getItem(hiddenColumnsStorageKey("raw-materials", "plan"))).toBe(
        JSON.stringify({ demandPlanTons: 1 }),
    );
});

test("an empty hidden set removes the storage entry", () => {
    writeStoredHiddenColumns("kppk", "products", { demandPlanTons: 1 });
    writeStoredHiddenColumns("kppk", "products", {});
    expect(window.localStorage.getItem(hiddenColumnsStorageKey("kppk", "products"))).toBeNull();
});

test("a malformed stored entry hides nothing", () => {
    window.localStorage.setItem(hiddenColumnsStorageKey("renovation", "plan"), "{not json");
    expect(readStoredHiddenColumns("renovation", "plan")).toEqual({});
});
