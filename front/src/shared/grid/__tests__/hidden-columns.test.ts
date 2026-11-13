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

const anchors = (entries: Array<[string, number, number]>): HiddenColumnAnchors =>
    Object.fromEntries(entries.map(([prop, level, group]) => [prop, { level, group }]));

test("adjacent hidden columns of ONE action form ONE block and reveal together", () => {
    const hidden = anchors([
        ["b", 1, 0],
        ["c", 1, 0],
        ["e", 0, 1],
    ]);
    expect(hiddenColumnBlocks(["a", "b", "c", "d", "e", "f"], hidden)).toEqual([
        { props: ["b", "c"], anchor: 1, priority: 0 },
        { props: ["e"], anchor: 0, priority: 1 },
    ]);
});

test("hiding an adjacent column at a higher level creates a second, top-level button", () => {
    const first = hideColumns({}, ["b"], 1);
    const second = hideColumns(first, ["c"], 0);
    expect(hiddenColumnBlocks(["a", "b", "c", "d"], second)).toEqual([
        { props: ["b"], anchor: 1, priority: 0 },
        { props: ["c"], anchor: 0, priority: 1 },
    ]);
});

test("two adjacent hide actions stay TWO buttons with a higher priority for the later one", () => {
    const first = hideColumns({}, ["b"], 1);
    const second = hideColumns(first, ["c"], 1);
    expect(hiddenColumnBlocks(["a", "b", "c", "d"], second)).toEqual([
        { props: ["b"], anchor: 1, priority: 0 },
        { props: ["c"], anchor: 1, priority: 1 },
    ]);
});

test("a group hide action keeps all its leaves together", () => {
    const hidden = hideColumns({}, ["b", "c"], 0);
    expect(hiddenColumnBlocks(["a", "b", "c", "d"], hidden)).toEqual([
        { props: ["b", "c"], anchor: 0, priority: 0 },
    ]);
});

test("revealing a block removes exactly its columns", () => {
    const hidden = hideColumns({}, ["b", "c", "e"], 1);
    const next = showColumns(hidden, ["b", "c"]);
    expect(Object.keys(next)).toEqual(["e"]);
    expect(hiddenColumnBlocks(["a", "b", "c", "d", "e"], next)).toEqual([
        { props: ["e"], anchor: 1, priority: 0 },
    ]);
});

test("a hidden column anchored at a higher header level keeps that level", () => {
    const hidden = hideColumns({}, ["demandPlanTons"], 0);
    expect(hiddenColumnBlocks(["demandPlanTons", "consumptionFactTons"], hidden)).toEqual([
        { props: ["demandPlanTons"], anchor: 0, priority: 0 },
    ]);
});

test("merging a leaf-hidden column with a higher-level block uses the higher level", () => {
    const hidden = anchors([
        ["groupLeft", 0, 0],
        ["leafRight", 5, 0],
    ]);
    expect(hiddenColumnBlocks(["groupLeft", "leafRight"], hidden)).toEqual([
        { props: ["groupLeft", "leafRight"], anchor: 0, priority: 0 },
    ]);
});

test("the table's hidden columns survive a reload and stay per table", () => {
    writeStoredHiddenColumns("raw-materials", "plan", anchors([["demandPlanTons", 1, 3]]));
    expect(readStoredHiddenColumns("raw-materials", "plan")).toEqual({
        demandPlanTons: { level: 1, group: 3 },
    });
    expect(readStoredHiddenColumns("raw-materials", "prices")).toEqual({});
    expect(window.localStorage.getItem(hiddenColumnsStorageKey("raw-materials", "plan"))).toBe(
        JSON.stringify({ demandPlanTons: { level: 1, group: 3 } }),
    );
});

test("a legacy numeric entry is read as its own action", () => {
    window.localStorage.setItem(
        hiddenColumnsStorageKey("raw-materials", "plan"),
        JSON.stringify({ b: 1, c: 1 }),
    );
    const hidden = readStoredHiddenColumns("raw-materials", "plan");
    expect(Object.keys(hidden)).toEqual(["b", "c"]);
    expect(hidden.b?.level).toBe(1);
    expect(hidden.c?.group).not.toBe(hidden.b?.group);
});

test("an empty hidden set removes the storage entry", () => {
    writeStoredHiddenColumns("kppk", "products", anchors([["demandPlanTons", 1, 0]]));
    writeStoredHiddenColumns("kppk", "products", {});
    expect(window.localStorage.getItem(hiddenColumnsStorageKey("kppk", "products"))).toBeNull();
});

test("a malformed stored entry hides nothing", () => {
    window.localStorage.setItem(hiddenColumnsStorageKey("renovation", "plan"), "{not json");
    expect(readStoredHiddenColumns("renovation", "plan")).toEqual({});
});
