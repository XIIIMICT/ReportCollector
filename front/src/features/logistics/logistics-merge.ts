import type { MergeData } from "@revolist/rv-pro-trial";

import type { LogisticsCountryRow, LogisticsTransportRow } from "@/shared/api/logistics";

/**
 * Merge the FIRST column over consecutive rows of one group.
 *
 * The backend returns the rows already grouped (market/type, then the sorted
 * label), so a run of consecutive rows with the same key becomes one tall left
 * cell. `disabled` covers both a user sorting (which reorders the rows) and any
 * active column filter (which hides rows inside a run): the merged ranges are
 * computed over the data array, so they cannot follow a filtered view.
 */
function groupedColumnMerges<T>(
    rows: readonly T[],
    groupKey: (row: T) => string,
    disabled: boolean,
): MergeData[] {
    if (disabled) return [];
    const sweeps: MergeData[] = [];
    let start = -1;
    const push = (from: number, to: number) => {
        if (from < 0 || to - from < 2) return;
        sweeps.push({ row: from, column: 0, rowSpan: to - from });
    };
    for (let index = 0; index < rows.length; index += 1) {
        const isGroupStart = index === 0 || groupKey(rows[index]!) !== groupKey(rows[index - 1]!);
        if (!isGroupStart) continue;
        if (start >= 0) push(start, index);
        start = index;
    }
    if (start >= 0) push(start, rows.length);
    return sweeps;
}

/** Merged ranges of the «Тип перевозки» cell in «Типы ТС и перевозки». */
export function transportTypeMerges(
    rows: readonly Pick<LogisticsTransportRow, "transportTypeId">[],
    disabled: boolean,
): MergeData[] {
    return groupedColumnMerges(rows, (row) => row.transportTypeId, disabled);
}

/** Merged ranges of the «Рынок» cell in «Страны». */
export function countryMarketMerges(
    rows: readonly Pick<LogisticsCountryRow, "market">[],
    disabled: boolean,
): MergeData[] {
    return groupedColumnMerges(rows, (row) => row.market, disabled);
}
