import { afterEach, beforeEach, expect, mock, spyOn, test } from "bun:test";

import { toastQueue } from "@heroui/react";
import type { ToastContentValue } from "@heroui/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import type { RenovationPlanImportApplyResult } from "@/shared/api/renovation";

type Job = RenovationPlanImportApplyResult;

const STORAGE_KEY = "rc.renovation.plan-import:renovation";

function job(overrides: Partial<Job> = {}): Job {
    return {
        id: "job-1",
        status: "running",
        progress: 0,
        total: 40,
        file_name: "plan.xlsb",
        error: null,
        new_rows: 0,
        updated_rows: 0,
        phase: "write",
        ...overrides,
    };
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((resolvePromise) => {
        resolve = resolvePromise;
    });
    return { promise, resolve };
}

// The hook is judged by what the footer and the toast receive: the API is
// replaced so the test controls when a poll answers, and storage is checked
// directly — that id is what a project round-trip re-attaches to.
const applyMock = mock(() => Promise.resolve(job()));
let nextJob: () => Promise<Job> = () => Promise.resolve(job());
const getJobMock = mock(() => nextJob());

const realImportApi = await import("@/shared/api/renovation-import-api");

mock.module("@/shared/api/renovation-import-api", () => ({
    ...realImportApi,
    applyRenovationPlanImport: applyMock,
    getRenovationPlanImportJob: getJobMock,
}));

const { usePlanImportJob } = await import("../use-plan-import-job");

type CapturedToast = {
    title?: unknown;
    description?: unknown;
    variant?: ToastContentValue["variant"] | undefined;
};

const toasts: CapturedToast[] = [];
let addSpy: ReturnType<typeof spyOn<typeof toastQueue, "add">> | undefined;

beforeEach(() => {
    toasts.length = 0;
    window.sessionStorage.clear();
    applyMock.mockReset();
    applyMock.mockImplementation(() => Promise.resolve(job()));
    getJobMock.mockReset();
    getJobMock.mockImplementation(() => nextJob());
    nextJob = () =>
        Promise.resolve(job({ status: "done", progress: 40, total: 40, phase: "done" }));
    addSpy = spyOn(toastQueue, "add").mockImplementation(((content: ToastContentValue) => {
        toasts.push(content);
        return "toast-id";
    }) as typeof toastQueue.add);
});

afterEach(() => {
    addSpy?.mockRestore();
    cleanup();
});

/**
 * The Excel load outlives its dialog: its bar lives in the footer, the job id
 * is kept in sessionStorage per project (so leaving the project and coming back
 * restores the bar), and only the terminal outcome is a toast.
 */

test("the load reports in the footer and settles as a toast", async () => {
    const applied: number[] = [];
    applyMock.mockImplementation(() =>
        Promise.resolve(job({ progress: 0, total: 40, phase: "write" })),
    );
    const firstPoll = deferred<Job>();
    let pollCount = 0;
    nextJob = () => {
        pollCount += 1;
        if (pollCount === 1) return firstPoll.promise;
        return Promise.resolve(
            job({
                status: "done",
                progress: 40,
                total: 40,
                phase: "done",
                new_rows: 3,
                updated_rows: 2,
            }),
        );
    };

    const view = renderHook(() =>
        usePlanImportJob({ projectKey: "renovation", onApplied: () => applied.push(1) }),
    );
    await act(async () => {
        void view.result.current.start(new File(["xlsb"], "plan.xlsb"), 10);
    });

    // The bar shows the accepted job before the first poll answers.
    expect(view.result.current.progress).toMatchObject({
        label: "Запись строк...",
        progress: 0,
        total: 40,
    });
    // The id is persisted before that poll, so a project switch can re-attach.
    await waitFor(() => expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe("job-1"));

    // The first poll moves the bar without clearing it.
    await act(async () => {
        firstPoll.resolve(job({ progress: 20, total: 40, phase: "write" }));
    });
    await waitFor(() => expect(view.result.current.progress?.progress).toBe(20));

    // The second poll is terminal: the bar goes away and the summary is a toast.
    await waitFor(() => expect(view.result.current.progress).toBeNull(), { timeout: 3000 });
    expect(applied).toHaveLength(1);
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(toasts.at(-1)).toMatchObject({
        title: "Файл загружен",
        variant: "success",
        description: "Новых строк: 3 · Обновлено строк: 2",
    });
});

test("a stored job re-attaches after the workspace remounts", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, "stored-job");
    const pending = deferred<Job>();
    nextJob = () => pending.promise;
    const applied: number[] = [];

    const view = renderHook(() =>
        usePlanImportJob({ projectKey: "renovation", onApplied: () => applied.push(1) }),
    );

    // The bar is back, unsized, before the first poll answers.
    await waitFor(() => expect(view.result.current.progress).not.toBeNull());
    expect(view.result.current.progress?.total).toBe(0);
    await waitFor(() => expect(getJobMock).toHaveBeenCalledWith("stored-job"));

    await act(async () => {
        pending.resolve(
            job({
                status: "done",
                progress: 40,
                total: 40,
                phase: "done",
                new_rows: 1,
                updated_rows: 0,
            }),
        );
    });

    await waitFor(() => expect(view.result.current.progress).toBeNull());
    expect(applied).toHaveLength(1);
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(toasts.at(-1)?.title).toBe("Файл загружен");
});

test("a second load is refused while one is running", async () => {
    const pending = deferred<Job>();
    nextJob = () => pending.promise;

    const view = renderHook(() => usePlanImportJob({ projectKey: "renovation" }));
    await act(async () => {
        void view.result.current.start(new File(["xlsb"], "first.xlsb"), 1);
    });
    applyMock.mockClear();

    await act(async () => {
        await view.result.current.start(new File(["xlsb"], "second.xlsb"), 1);
    });

    expect(applyMock).not.toHaveBeenCalled();
    expect(toasts.at(-1)?.title).toBe("Загрузка уже выполняется");
});
