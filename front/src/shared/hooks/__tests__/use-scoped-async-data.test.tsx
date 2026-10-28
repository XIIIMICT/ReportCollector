import { afterEach, expect, test } from "bun:test";

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { useScopedAsyncData } from "../use-scoped-async-data";

afterEach(cleanup);

test("retains the old result as stale and ignores a late previous scope", async () => {
    let resolveOld!: (value: string) => void;
    let resolveNew!: (value: string) => void;
    const oldRequest = new Promise<string>((resolve) => {
        resolveOld = resolve;
    });
    const newRequest = new Promise<string>((resolve) => {
        resolveNew = resolve;
    });
    const { result, rerender } = renderHook(
        ({ scope }: { scope: string }) =>
            useScopedAsyncData(scope, () => (scope === "old" ? oldRequest : newRequest), [scope]),
        { initialProps: { scope: "old" } },
    );

    await act(async () => resolveOld("old rows"));
    await waitFor(() => expect(result.current.loadedScope).toBe("old"));
    rerender({ scope: "new" });
    expect(result.current.data).toBe("old rows");
    expect(result.current.loadedScope).toBe("old");
    expect(result.current.loading).toBe(true);

    await act(async () => {
        resolveOld("late old rows");
        resolveNew("new rows");
    });
    await waitFor(() => expect(result.current.loadedScope).toBe("new"));
    expect(result.current.data).toBe("new rows");
});
