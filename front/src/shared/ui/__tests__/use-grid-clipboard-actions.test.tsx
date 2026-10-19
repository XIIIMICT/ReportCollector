import { afterEach, expect, test } from "bun:test";

import { cleanup, renderHook } from "@testing-library/react";

import type { ClipboardActions } from "../text-helpers";
import { useGridClipboardActions } from "../use-grid-clipboard-actions";

afterEach(cleanup);

test("clipboard publisher and actions keep their identity across renders", () => {
    const gridRef = { current: { copySelection: () => {}, pasteIntoSelection: () => {} } };
    const publications: ClipboardActions[] = [];
    const publish = (actions: ClipboardActions) => {
        publications.push(actions);
    };
    const { result, rerender } = renderHook(() => useGridClipboardActions(gridRef, publish));
    const first = result.current;
    first({ canPaste: false });
    rerender();
    expect(result.current).toBe(first);
    result.current({ canPaste: true });
    expect(publications[1]?.onCopy).toBe(publications[0]?.onCopy);
    expect(publications[1]?.onPaste).toBe(publications[0]?.onPaste);
});

test("stored actions resolve the current handle instead of a stale mounted grid", () => {
    const calls: string[] = [];
    const gridRef = {
        current: {
            copySelection: () => {
                calls.push("old");
            },
            pasteIntoSelection: () => {},
        },
    };
    let actions: ClipboardActions | undefined;
    const { result } = renderHook(() =>
        useGridClipboardActions(gridRef, (next) => {
            actions = next;
        }),
    );
    result.current({ canPaste: false });
    gridRef.current = {
        copySelection: () => {
            calls.push("new");
        },
        pasteIntoSelection: () => {},
    };
    actions?.onCopy();
    expect(calls).toEqual(["new"]);
});
