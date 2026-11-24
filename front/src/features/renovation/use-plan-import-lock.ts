"use client";

import { useEffect, useState } from "react";

import { getRenovationActivePlanImport } from "@/shared/api/renovation";

/** The red-chip state of the plan form while somebody is loading a workbook. */
export type PlanImportLock = {
    userName: string;
    progressPercent: number;
};

const POLL_INTERVAL_MS = 1500;

/**
 * Gate A: the whole «Пообъектный план» form is read-only while an import runs.
 *
 * The durable job row in PostgreSQL is the source of truth; a poll is the
 * status broadcast. A missed poll only delays showing the chip, it never
 * permits a conflicting write (the server owns the policy too).
 */
export function usePlanImportLock(enabled: boolean): PlanImportLock | null {
    const [lock, setLock] = useState<PlanImportLock | null>(null);

    useEffect(() => {
        if (!enabled) {
            setLock(null);
            return;
        }
        let cancelled = false;
        const check = async () => {
            try {
                const job = await getRenovationActivePlanImport();
                if (cancelled) return;
                if (job === null || job.status === "done" || job.status === "failed") {
                    setLock(null);
                    return;
                }
                setLock({
                    userName: job.user_name || "Другой пользователь",
                    progressPercent:
                        job.total > 0 ? Math.round((job.progress / job.total) * 100) : 0,
                });
            } catch {
                // A transient poll failure keeps the previous state and retries.
            }
        };
        void check();
        const timer = window.setInterval(() => void check(), POLL_INTERVAL_MS);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [enabled]);

    return lock;
}
