import { describe, expect, test } from "bun:test";

import { act, cleanup, renderHook } from "@testing-library/react";

import {
    DEFAULT_VIEW_STATE,
    clearStoredViewState,
    readStoredViewState,
    useViewState,
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

test("row height accepts 18–72, rejects out-of-range values and resets to 36", () => {
    const key = "reportcollector:table-dimensions:row-height-test";
    window.localStorage.removeItem(key);
    const { result, unmount } = renderHook(() => useViewState("row-height-test"));
    expect(result.current.rowHeight).toBe(36);
    act(() => result.current.setRowHeight(18));
    expect(result.current.rowHeight).toBe(18);
    act(() => result.current.setRowHeight(17));
    expect(result.current.rowHeight).toBe(18);
    act(() => result.current.setRowHeight(72));
    expect(result.current.rowHeight).toBe(72);
    act(() => result.current.setRowHeight(73));
    expect(result.current.rowHeight).toBe(72);
    act(() => result.current.resetRowHeight());
    expect(result.current.rowHeight).toBe(36);
    unmount();
    const restored = renderHook(() => useViewState("row-height-test"));
    expect(restored.result.current.rowHeight).toBe(36);
    cleanup();
    window.localStorage.removeItem(key);
});
