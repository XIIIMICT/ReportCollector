import { toast } from "@heroui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { cancelTableExportJob, getTableExportJob } from "@/shared/api/table-export";
import type { ExportColumn } from "@/shared/grid/grid-config";

import { triggerNativeDownload } from "./download";
import { runTableExport, type GridExportView } from "./table-export-action";
import { errorMessage } from "./text-helpers";

/** «вся таблица» — every row of the view; «видимая» — what the grid shows. */
export type ExportMode = "all" | "visible";

/**
 * The request view handed to a call site.
 *
 * `rowIds` is a list for «видимая» and `null` for «вся» — already resolved by
 * the hook, so a call site only forwards it and never re-derives the mode.
 */
export type ExportRequestView = {
    columns: ExportColumn[];
    rowIds: string[] | null;
    mergeRows: boolean;
    valuesOnly: boolean;
};

type ActiveJob = { id: string; baseUrl: string; fileUrl: string; fileName: string };

const POLL_INTERVAL_MS = 750;

/**
 * One export flow for every grid: pending state, the grid snapshot, the file
 * name, the request, and DocMasker-style cancellation of a durable job.
 *
 * The `rowIds` rule stays here: «видимая» sends the grid's own rows IN ITS
 * ORDER, «вся» sends null. `valuesOnly` travels in the same request: computed
 * cells are written as values by default, so exports are smaller/faster.
 */
export function useTableExport({
    gridRef,
    fileName,
    request,
}: {
    gridRef: { current: { getExportState: () => Promise<GridExportView | null> } | null };
    /** The download's file name; receives the mode so a form may distinguish them. */
    fileName: (mode: ExportMode) => string;
    /** Build and perform the request; `view.rowIds`/`valuesOnly` are mode-resolved. */
    request: (
        view: ExportRequestView,
        fileName: string,
        signal?: AbortSignal,
    ) => Promise<
        Blob | { fileUrl: string; jobsBaseUrl: string; job: { id: string; fileName: string } }
    >;
}): {
    pending: boolean;
    exportTable: (mode: ExportMode, valuesOnly?: boolean) => void;
    cancel: (() => void) | undefined;
} {
    const [pending, setPending] = useState(false);
    const [activeJob, setActiveJob] = useState<ActiveJob | null>(null);
    const fileNameRef = useRef(fileName);
    fileNameRef.current = fileName;
    const requestRef = useRef(request);
    requestRef.current = request;
    const pendingRef = useRef(pending);
    pendingRef.current = pending;
    const postController = useRef<AbortController | null>(null);
    const pollController = useRef<AbortController | null>(null);
    const timer = useRef<number | null>(null);
    const activeJobRef = useRef<ActiveJob | null>(null);

    const end = useCallback(() => {
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = null;
        pollController.current = null;
        postController.current = null;
        activeJobRef.current = null;
        setActiveJob(null);
        setPending(false);
    }, []);

    const cancel = useCallback(() => {
        const job = activeJobRef.current;
        if (job === null) return;
        void cancelTableExportJob(job.baseUrl, job.id);
        pollController.current?.abort();
        end();
        toast("Выгрузка отменена");
    }, [end]);

    const startPolling = useCallback(
        (job: ActiveJob) => {
            activeJobRef.current = job;
            setActiveJob(job);
            setPending(true);
            pollController.current = new AbortController();
            const poll = async (): Promise<void> => {
                try {
                    const status = await getTableExportJob(
                        job.baseUrl,
                        job.id,
                        pollController.current?.signal,
                    );
                    if (status.status === "done") {
                        triggerNativeDownload(job.fileUrl, job.fileName);
                        toast.success("Файл выгружен", { description: job.fileName });
                        end();
                        return;
                    }
                    if (status.status === "failed") {
                        toast.danger("Не удалось выгрузить файл", {
                            description: status.error ?? undefined,
                        });
                        end();
                        return;
                    }
                    timer.current = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
                } catch (reason) {
                    if (pollController.current?.signal.aborted !== true) {
                        toast.danger("Не удалось выгрузить файл", {
                            description: errorMessage(reason),
                        });
                        end();
                    }
                }
            };
            void poll();
        },
        [end],
    );

    useEffect(
        () => () => {
            pollController.current?.abort();
            postController.current?.abort();
            if (timer.current !== null) window.clearTimeout(timer.current);
        },
        [],
    );

    const exportTable = useCallback(
        (mode: ExportMode, valuesOnly = true) => {
            postController.current = new AbortController();
            void runTableExport({
                pending: pendingRef.current,
                gridRef,
                setPending,
                fileName: () => fileNameRef.current(mode),
                request: (view, name) =>
                    requestRef.current(
                        {
                            columns: view.columns,
                            rowIds: mode === "visible" ? view.rowIds : null,
                            mergeRows: view.mergeRows ?? true,
                            valuesOnly,
                        },
                        name,
                        postController.current?.signal,
                    ),
                signal: postController.current.signal,
                onJob: (result) =>
                    startPolling({
                        id: result.job.id,
                        baseUrl: result.jobsBaseUrl,
                        fileUrl: result.fileUrl,
                        fileName: result.job.fileName,
                    }),
            });
        },
        [gridRef, startPolling],
    );

    return { pending, exportTable, cancel: activeJob !== null ? cancel : undefined };
}
