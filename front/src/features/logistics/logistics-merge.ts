import type { MergeData } from "@revolist/rv-pro-trial";

import type { LogisticsTransportRow } from "@/shared/api/logistics";

/**
 * Merged ranges of the «Тип перевозки» cell in the «Типы ТС и перевозки» table.
 *
 * The backend returns the rows already grouped (transport type, then vehicle
 * name), so a run of consecutive rows with the same `transportTypeId` becomes
 * one tall left cell. A user sorting reorders the rows and breaks the runs, so
 * the merge is disabled while sorting is active — the same rule as the
 * «Продуктовые позиции» product block in «КППК».
 */
export function transportTypeMerges(
    rows: readonly Pick<LogisticsTransportRow, "transportTypeId">[],
    sorted: boolean,
): MergeData[] {
    if (sorted) return [];
    const sweeps: MergeData[] = [];
    let start = -1;
    const push = (from: number, to: number) => {
        if (from < 0 || to - from < 2) return;
        sweeps.push({ row: from, column: 0, rowSpan: to - from });
    };
    for (let index = 0; index < rows.length; index += 1) {
        const isGroupStart =
            index === 0 || rows[index]!.transportTypeId !== rows[index - 1]!.transportTypeId;
        if (!isGroupStart) continue;
        if (start >= 0) push(start, index);
        start = index;
    }
    if (start >= 0) push(start, rows.length);
    return sweeps;
}
