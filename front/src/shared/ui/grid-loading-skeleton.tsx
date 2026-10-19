"use client";

import { Skeleton } from "@heroui/react";

/**
 * The loading state every grid shows on its FIRST load — one shape in all
 * projects (the plan, the dictionaries, «КППК» and «Реализация»).
 *
 * It is a miniature of the table, not a spinner: a narrow row-number column, a
 * rule, a wide data column, a rule, then a few rows — the same block geometry
 * the grid mounts into, so the layout doesn't jump when the rows arrive.
 * Server-side and background refreshes keep the grid mounted and never show it
 * (unmounting would reset the scroll position).
 */
export function GridLoadingSkeleton() {
    return (
        <div className="grid-viewport" aria-busy>
            <div className="grid-loading-skeleton" role="status" aria-label="Загрузка таблицы">
                <div className="grid-loading-skeleton__row grid-loading-skeleton__row--head">
                    <div className="grid-loading-skeleton__cell grid-loading-skeleton__cell--head">
                        <Skeleton className="grid-loading-skeleton__block" />
                    </div>
                    <div className="grid-loading-skeleton__cell">
                        <Skeleton className="grid-loading-skeleton__block" />
                    </div>
                </div>
                {[0, 1, 2, 3].map((row) => (
                    <div key={row} className="grid-loading-skeleton__row">
                        <div className="grid-loading-skeleton__cell grid-loading-skeleton__cell--head">
                            <Skeleton className="grid-loading-skeleton__block" />
                        </div>
                        <div className="grid-loading-skeleton__cell">
                            <Skeleton className="grid-loading-skeleton__block" />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
