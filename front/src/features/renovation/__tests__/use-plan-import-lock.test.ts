import { expect, mock, test } from "bun:test";

import { renderHook, waitFor } from "@testing-library/react";

import type { RenovationPlanImportApplyResult } from "@/shared/api/renovation";

type Job = RenovationPlanImportApplyResult;

let nextJob: Job | null = null;
const activeMock = mock(() => Promise.resolve(nextJob));

mock.module("@/shared/api/renovation", () => ({
    getRenovationActivePlanImport: activeMock,
}));

const { usePlanImportLock } = await import("../use-plan-import-lock");

test("Gate A: a running import locks the form with the importer and progress", async () => {
    nextJob = null;
    const empty = renderHook(() => usePlanImportLock(true));
    await waitFor(() => expect(empty.result.current).toBeNull());

    nextJob = {
        id: "job-1",
        status: "running",
        progress: 10,
        total: 40,
        file_name: "plan.xlsb",
        user_name: "Иванов Иван",
        error: null,
        new_rows: 0,
        updated_rows: 0,
        phase: "write",
    };
    const locked = renderHook(() => usePlanImportLock(true));
    await waitFor(() =>
        expect(locked.result.current).toEqual({ userName: "Иванов Иван", progressPercent: 25 }),
    );

    nextJob = { ...nextJob, status: "done", progress: 40 };
    const finished = renderHook(() => usePlanImportLock(true));
    await waitFor(() => expect(finished.result.current).toBeNull());
});

test("a disabled lock stays empty", () => {
    const disabled = renderHook(() => usePlanImportLock(false));
    expect(disabled.result.current).toBeNull();
});
