"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    applyRenovationPlanImport,
    getRenovationPlanImportJob,
    type RenovationPlanImportApplyResult,
} from "@/shared/api/renovation";
import type { FooterProgressState } from "@/shared/ui/footer-progress";
import { errorMessage } from "@/shared/ui/text-helpers";

const POLL_INTERVAL_MS = 750;
const STORAGE_PREFIX = "rc.renovation.plan-import";

/** This session's running import of one project, so a return can re-attach. */
export function planImportStorageKey(projectKey: string): string {
    return `${STORAGE_PREFIX}:${projectKey}`;
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function stageLabel(phase: string | null): string {
    switch (phase) {
        case "parse":
            return "Разбор файла...";
        case "write":
            return "Запись строк...";
        case "recompute":
            return "Пересчёт формул...";
        default:
            return "Идёт загрузка в базу...";
    }
}

function progressOf(
    job: Pick<RenovationPlanImportApplyResult, "phase" | "progress" | "total">,
): FooterProgressState {
    return { label: stageLabel(job.phase), progress: job.progress, total: job.total };
}

function readStoredJob(storageKey: string): string | null {
    try {
        return window.sessionStorage.getItem(storageKey);
    } catch {
        // Private mode / disabled storage: the job still runs, it just cannot
        // be re-attached after a project round-trip.
        return null;
    }
}

function rememberJob(storageKey: string, id: string): void {
    try {
        window.sessionStorage.setItem(storageKey, id);
    } catch {
        // See readStoredJob: progress still works inside this mount.
    }
}

function forgetJob(storageKey: string): void {
    try {
        window.sessionStorage.removeItem(storageKey);
    } catch {
        // Nothing to clean when storage is unavailable.
    }
}

type TrackedJob = { cancelled: boolean };

/**
 * Tracks the XLSB load in the footer instead of a toast.
 *
 * The job outlives the dialog (which closes at once) and the project switch:
 * its id is kept in sessionStorage per project, so coming back re-attaches and
 * shows the bar again until the job settles. Tab switches inside the project
 * keep the same workspace instance and therefore the same bar. Only the
 * terminal success/error is a toast.
 */
export function usePlanImportJob({
    projectKey,
    onApplied,
}: {
    projectKey: string;
    /** Refetches the grid once the load has been written. */
    onApplied?: () => void;
}): {
    progress: FooterProgressState | null;
    start: (file: File, expectedRows: number) => Promise<void>;
} {
    const [progress, setProgress] = useState<FooterProgressState | null>(null);
    const storageKey = planImportStorageKey(projectKey);
    const onAppliedRef = useRef(onApplied);
    onAppliedRef.current = onApplied;
    const trackedRef = useRef<TrackedJob | null>(null);

    const settle = useCallback((job: RenovationPlanImportApplyResult, key: string) => {
        forgetJob(key);
        trackedRef.current = null;
        setProgress(null);
        if (job.status === "done") {
            onAppliedRef.current?.();
            toast.success("Файл загружен", {
                description: `Новых строк: ${job.new_rows} · Обновлено строк: ${job.updated_rows}`,
            });
            return;
        }
        toast.danger("Не удалось загрузить файл", {
            description: job.error ?? "Не удалось загрузить файл.",
        });
    }, []);

    const fail = useCallback((reason: unknown, key: string) => {
        forgetJob(key);
        trackedRef.current = null;
        setProgress(null);
        toast.danger("Не удалось загрузить файл", { description: errorMessage(reason) });
    }, []);

    const poll = useCallback(
        async (id: string, tracked: TrackedJob, key: string) => {
            try {
                let job = await getRenovationPlanImportJob(id);
                while (!tracked.cancelled) {
                    if (job.status === "done" || job.status === "failed") {
                        settle(job, key);
                        return;
                    }
                    setProgress(progressOf(job));
                    await sleep(POLL_INTERVAL_MS);
                    if (tracked.cancelled) return;
                    job = await getRenovationPlanImportJob(id);
                }
            } catch (reason) {
                if (!tracked.cancelled) fail(reason, key);
            }
        },
        [fail, settle],
    );

    // Re-attach to a job this session started for the project before the
    // workspace was unmounted (a project round-trip or a page reload).
    useEffect(() => {
        const storedId = readStoredJob(storageKey);
        const tracked: TrackedJob = { cancelled: false };
        if (storedId) {
            trackedRef.current = tracked;
            // The bar comes back unsized; the first poll supplies the phase
            // and the server's total.
            setProgress({ label: stageLabel(null), progress: 0, total: 0 });
            void poll(storedId, tracked, storageKey);
        }
        return () => {
            // The id stays in sessionStorage, so a return re-attaches to it.
            if (trackedRef.current) {
                trackedRef.current.cancelled = true;
                trackedRef.current = null;
            }
        };
    }, [poll, storageKey]);

    const start = useCallback(
        async (file: File, expectedRows: number) => {
            if (trackedRef.current) {
                // One load at a time: the grid behind is being written by the
                // running job, and the footer already shows its bar.
                toast("Загрузка уже выполняется", {
                    description: "Дождитесь завершения текущей загрузки.",
                });
                return;
            }
            const tracked: TrackedJob = { cancelled: false };
            trackedRef.current = tracked;
            setProgress({ label: stageLabel(null), progress: 0, total: 0 });
            try {
                const accepted = await applyRenovationPlanImport(file, expectedRows);
                if (accepted.status === "done" || accepted.status === "failed") {
                    if (!tracked.cancelled) settle(accepted, storageKey);
                    return;
                }
                // Persist BEFORE polling: a project switch must be able to
                // re-attach even if it happens on the next tick.
                rememberJob(storageKey, accepted.id);
                if (tracked.cancelled) return;
                setProgress(progressOf(accepted));
                await poll(accepted.id, tracked, storageKey);
            } catch (reason) {
                if (!tracked.cancelled) fail(reason, storageKey);
            }
        },
        [fail, poll, settle, storageKey],
    );

    return { progress, start };
}
