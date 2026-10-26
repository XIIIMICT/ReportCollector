"use client";

import { DateFormatter, type DateValue, getLocalTimeZone } from "@internationalized/date";

import type { FacetDateRange } from "@/shared/ui/column-filter-facets";
import { DateTimePicker, fromPickerValue, toPickerValue } from "@/shared/ui/date-time-picker";

/**
 * The date half of a column filter's search: a MIN — MAX pair, one above the
 * other.
 *
 * A text query over a date column is the wrong tool for the same reason it is
 * for numbers (see `column-filter-range.tsx`): «08» matches every value that
 * merely contains those digits. A range is what the column is actually filtered
 * by, and the popover asks the SOURCE for the whole matching set.
 *
 * Both bounds are optional and independent — only a minimum means «от него и
 * выше», only a maximum means «до него включительно».
 *
 * TWO KINDS, one component:
 * - `kind="datetime"` — timestamp columns (`createdAt`). The picker shows the
 *   time with `granularity="second"`, because the cells print seconds and a
 *   day-only picker would silently drop them from the bound.
 * - `kind="date"` — plain date columns (the plan's «Дата начала/окончания
 *   работ»). No time at all: the cell holds a calendar day, so a time would be a
 *   fiction the user could not match against the column.
 *
 * The stored value is epoch ms in both cases. For `kind="date"` the epoch is
 * LOCAL MIDNIGHT of the chosen day — the same instant `parseFacetInstant` derives
 * from the cell's «03.02.2026» text, which is what makes the comparison agree.
 */
export const EMPTY_DATE_RANGE: FacetDateRange = { min: null, max: null };

/** Does the range impose no constraint at all? */
export function isDateRangeEmpty(range: FacetDateRange): boolean {
    return range.min === null && range.max === null;
}

/** `date` — a plain calendar day; `datetime` — a timestamp. */
export type DateFilterKind = "date" | "datetime";

/**
 * Convert a stored epoch-ms bound into a picker value.
 *
 * `DatePicker` speaks `@internationalized/date` values, the filter stores epoch
 * ms — one conversion, here, so the rest of the popover never deals with
 * calendar objects. `parseAbsoluteToLocal` maps the instant into the browser's
 * zone, which is the one the cells print. A date column narrows that to the DAY:
 * its picker has no time segments to fill.
 */
export const toDateValue = toPickerValue;

/**
 * Convert a picker value back into the stored epoch-ms bound.
 *
 * A cleared picker yields `null` («no bound»), not `NaN` — the filter treats a
 * missing bound as «unconstrained», and every comparison against `NaN` is false,
 * which would silently empty the table. For a date the local midnight of the day
 * is the bound, matching how the cell's text is parsed back.
 */
export const fromDateValue = fromPickerValue;

/**
 * Two pickers with a dash between them — the filter's search field for a date or
 * timestamp column. The parent lays them out ONE ABOVE THE OTHER, so the pair
 * reads as a single control. Each one is the app's shared `DateTimePicker`, which
 * brings the calendar and (for a timestamp) the «Время:» row inside it.
 */
export function ColumnFilterDateRange({
    columnLabel,
    value,
    onChange,
    kind = "datetime",
}: {
    /** The column's header text, for the fields' accessible names. */
    columnLabel: string;
    value: FacetDateRange;
    onChange: (next: FacetDateRange) => void;
    /** Date-only grid cells versus timestamps. */
    kind?: DateFilterKind;
}) {
    const bounds: { slot: "min" | "max"; label: string; text: string }[] = [
        { slot: "min", label: "мин", text: "Минимум" },
        { slot: "max", label: "макс", text: "Максимум" },
    ];
    return (
        <div
            className="column-filter__date-range"
            role="group"
            aria-label={`Диапазон: ${columnLabel}`}
        >
            {bounds.map(({ slot, label, text }) => (
                <DateTimePicker
                    key={slot}
                    className="column-filter__date-row"
                    fieldClassName="column-filter__date-field"
                    label={label}
                    ariaLabel={`${text}: ${columnLabel}`}
                    kind={kind}
                    value={value[slot]}
                    onChange={(next) => onChange({ ...value, [slot]: next })}
                />
            ))}
        </div>
    );
}

/** The app's timestamp format — the cells' own, so the two never disagree. */
const CELL_FORMAT = new DateFormatter("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
});

/** The same day without a time — the date cells' format. */
const DAY_FORMAT = new DateFormatter("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
});

/** The cell's text for a picker value — used by the popover's own hints. */
export function formatFilterDate(
    value: DateValue | null,
    kind: DateFilterKind = "datetime",
): string {
    if (value === null) return "";
    const date = value.toDate(getLocalTimeZone());
    return (kind === "date" ? DAY_FORMAT : CELL_FORMAT).format(date);
}
