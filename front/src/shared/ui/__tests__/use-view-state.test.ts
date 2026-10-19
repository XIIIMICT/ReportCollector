import { describe, expect, test } from "bun:test";

import {
    DEFAULT_VIEW_STATE,
    clearStoredViewState,
    readStoredViewState,
} from "@/shared/ui/use-view-state";

describe("view state persistence", () => {
    test("a missing entry falls back to the defaults", () => {
        clearStoredViewState("raw_materials");
        expect(readStoredViewState("raw_materials")).toEqual(DEFAULT_VIEW_STATE);
    });

    test("a stored value is read back", () => {
        window.localStorage.setItem(
            "reportcollector:view:raw_materials",
            JSON.stringify({ headerPinned: false, showServiceColumns: true }),
        );
        expect(readStoredViewState("raw_materials")).toEqual({
            headerPinned: false,
            showServiceColumns: true,
        });
        clearStoredViewState("raw_materials");
    });

    test("a partially written entry keeps the defaults for the missing field", () => {
        window.localStorage.setItem(
            "reportcollector:view:renovation",
            JSON.stringify({ headerPinned: false }),
        );
        expect(readStoredViewState("renovation")).toEqual({
            headerPinned: false,
            showServiceColumns: DEFAULT_VIEW_STATE.showServiceColumns,
        });
        clearStoredViewState("renovation");
    });

    test("corrupted JSON does not throw", () => {
        window.localStorage.setItem("reportcollector:view:kppk", "{not json");
        expect(readStoredViewState("kppk")).toEqual(DEFAULT_VIEW_STATE);
        clearStoredViewState("kppk");
    });

    test("projects do not share their view state", () => {
        window.localStorage.setItem(
            "reportcollector:view:raw_materials",
            JSON.stringify({ headerPinned: false, showServiceColumns: true }),
        );
        expect(readStoredViewState("kppk")).toEqual(DEFAULT_VIEW_STATE);
        clearStoredViewState("raw_materials");
    });
});
