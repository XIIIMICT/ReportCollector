"use client";

import { Label, ProgressBar } from "@heroui/react";

/** What a background operation reports to the footer's center slot. */
export type FooterProgressState = {
    /** Stage caption; also the bar's accessible name. */
    label: string;
    /** Units done — 0 until the server sizes the work. */
    progress: number;
    /** The total unit count; 0 means the work is not sized yet. */
    total: number;
};

/**
 * A long-running background operation in the footer, between the form status
 * and the action buttons.
 *
 * The label names the current stage and the output states the percentage; the
 * wrapper (`.table-footer__progress`) is bounded at 20em — 10em once the view
 * strip's label collapses — and shrinks under pressure, so the bar never
 * pushes the actions out and never disappears. Until the first server reply
 * it only shows motion: there is no percentage to state yet.
 */
export function FooterProgress({ label, progress, total }: FooterProgressState) {
    const determinate = total > 0;
    return (
        <div className="table-footer__progress">
            <ProgressBar
                aria-label={label}
                className="table-footer__progress-bar"
                // React Aria defaults a missing `value` to 0 (determinate), so
                // the indeterminate state has to be requested explicitly.
                isIndeterminate={!determinate}
                maxValue={determinate ? total : 100}
                value={determinate ? Math.max(0, Math.min(progress, total)) : 0}
            >
                <Label>{label}</Label>
                {determinate ? <ProgressBar.Output /> : null}
                <ProgressBar.Track>
                    <ProgressBar.Fill />
                </ProgressBar.Track>
            </ProgressBar>
        </div>
    );
}
