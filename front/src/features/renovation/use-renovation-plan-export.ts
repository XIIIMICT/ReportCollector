"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    cancelRenovationExportJob,
    getRenovationExportJob,
    createRenovationExportJob,
    renovationExportJobFileUrl,
    type RenovationPlanExportParams,
} from "@/shared/api/renovation";
import { triggerNativeDownload } from "@/shared/ui/download";
import { errorMessage } from "@/shared/ui/text-helpers";

const POLL_INTERVAL_MS = 750;

/**
 * Renovation plan export uses its own job API (the plan is filtered/scoped
 * server-side), but offers the same DocMasker-style cancellation as every
 * table export: the button becomes «Отменить» while the job runs.
 */
export function useRenovationPlanExport(onError?: (reason: unknown) => boolean): {
    pending: boolean;
    start: (params: RenovationPlanExportParams) => void;
    cancel: (() => void) | undefined;
} {
    const [pending, setPending] = useState(false);
    const [activeId, setActiveId] = useState<string | null>(null);
    const activeRef = useRef<string | null>(null);
    const pollRef = useRef<AbortController | null>(null);
    const timerRef = useRef<number | null>(null);

    const end = useCallback(() => {
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = null;
        pollRef.current = null;
        activeRef.current = null;
        setActiveId(null);
        setPending(false);
    }, []);

    const cancel = useCallback(() => {
        const id = activeRef.current;
        if (id === null) return;
        void cancelRenovationExportJob(id);
        pollRef.current?.abort();
        end();
        toast("Выгрузка отменена");
    }, [end]);

    const start = useCallback(
        (params: RenovationPlanExportParams) => {
            if (activeRef.current !== null) return;
            setPending(true);
            void (async () => {
                try {
                    const job = await createRenovationExportJob(params);
                    if (job.status === "failed")
                        throw new Error(job.error ?? "Не удалось сформировать файл.");
                    activeRef.current = job.id;
                    setActiveId(job.id);
                    pollRef.current = new AbortController();
                    const poll = async (): Promise<void> => {
                        try {
                            const status = await getRenovationExportJob(job.id);
                            if (status.status === "done") {
                                triggerNativeDownload(
                                    renovationExportJobFileUrl(job.id),
                                    status.fileName,
                                );
                                toast.success("Файл выгружен", { description: status.fileName });
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
                            timerRef.current = window.setTimeout(
                                () => void poll(),
                                POLL_INTERVAL_MS,
                            );
                        } catch (reason) {
                            if (pollRef.current?.signal.aborted === true) return;
                            if (onError?.(reason) === true) {
                                end();
                                return;
                            }
                            toast.danger("Не удалось выгрузить файл", {
                                description: errorMessage(reason),
                            });
                            end();
                        }
                    };
                    void poll();
                } catch (reason) {
                    if (onError?.(reason) === true) {
                        end();
                        return;
                    }
                    toast.danger("Не удалось выгрузить файл", {
                        description: errorMessage(reason),
                    });
                    end();
                }
            })();
        },
        [end, onError],
    );

    useEffect(
        () => () => {
            pollRef.current?.abort();
            if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        },
        [],
    );

    return { pending, start, cancel: activeId !== null ? cancel : undefined };
}
