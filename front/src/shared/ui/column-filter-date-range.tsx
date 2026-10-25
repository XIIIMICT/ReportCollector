"use client";

import { Calendar, DateField, DatePicker } from "@heroui/react";
import {
    type CalendarDateTime,
    DateFormatter,
    type DateValue,
    getLocalTimeZone,
    parseAbsoluteToLocal,
    toCalendarDateTime,
} from "@internationalized/date";

import type { FacetDateRange } from "@/shared/ui/column-filter-facets";

/**
 * The timestamp half of a column filter's search: a MIN — MAX pair of date-time
 * pickers, one above the other.
 *
 * A text query over a timestamp column is the wrong tool for the same reason it
 * is for numbers (see `column-filter-range.tsx`): «08» matches every value that
 * merely contains those digits. A range is what the column is actually filtered
 * by, and the popover asks the SOURCE for the whole matching set.
 *
 * Both bounds are optional and independent — only a minimum means «от него и
 * выше», only a maximum means «до него включительно».
 *
 * SHARED FORMAT, so the two pickers and the cells agree:
 * - `granularity="second"` — the cells print seconds, a day-only picker would
 *   silently drop them from the bound;
 * - `hourCycle={24}` — the cells print 24-hour time;
 * - the time zone is NOT shown. The value is a local instant and the cells show
 *   local time, so an extra «GMT+3» label would only add noise. `SHOW_TIME_ZONE`
 *   keeps the flag the pickers accept, ready for the day timestamps stop being
 *   zoned to the browser.
 */
export const EMPTY_DATE_RANGE: FacetDateRange = { min: null, max: null };

/** Show the picker's time-zone suffix? Kept as a flag, off for now. */
export const SHOW_TIME_ZONE = false;

/** Does the range impose no constraint at all? */
export function isDateRangeEmpty(range: FacetDateRange): boolean {
    return range.min === null && range.max === null;
}

/**
 * Convert a stored epoch-ms bound into a picker value.
 *
 * `DatePicker` speaks `@internationalized/date` values, the filter stores epoch
 * ms — one conversion, here, so the rest of the popover never deals with
 * calendar objects. `parseAbsoluteToLocal` maps the instant into the browser's
 * zone, which is the one the cells print.
 */
export function toDateValue(epochMs: number | null): CalendarDateTime | null {
    if (epochMs === null) return null;
    const parsed = parseAbsoluteToLocal(new Date(epochMs).toISOString());
    return toCalendarDateTime(parsed);
}

/**
 * Convert a picker value back into the stored epoch-ms bound.
 *
 * A cleared picker yields `null` («no bound»), not `NaN` — the filter treats a
 * missing bound as «unconstrained», and every comparison against `NaN` is false,
 * which would silently empty the table.
 */
export function fromDateValue(next: DateValue | null): number | null {
    return next === null ? null : next.toDate(getLocalTimeZone()).getTime();
}

/**
 * Two date-time pickers with a dash between them — the filter's search field for
 * a timestamp column. The parent lays them out ONE ABOVE THE OTHER (a date plus
 * its time needs the width), so the row reads as a single control.
 */
export function ColumnFilterDateRange({
    columnLabel,
    value,
    onChange,
}: {
    /** The column's header text, for the fields' accessible names. */
    columnLabel: string;
    value: FacetDateRange;
    onChange: (next: FacetDateRange) => void;
}) {
    const bound = fromDateValue;
    return (
        <div
            className="column-filter__date-range"
            role="group"
            aria-label={`Диапазон: ${columnLabel}`}
        >
            <div className="column-filter__date-row">
                <span className="column-filter__date-label" aria-hidden="true">
                    мин:
                </span>
                <DatePicker
                    className="column-filter__date-field"
                    aria-label={`Минимум: ${columnLabel}`}
                    value={toDateValue(value.min)}
                    onChange={(next) => onChange({ ...value, min: bound(next) })}
                    granularity="second"
                    hourCycle={24}
                    hideTimeZone={!SHOW_TIME_ZONE}
                    shouldForceLeadingZeros
                >
                    <DateField.Group fullWidth variant="secondary">
                        <DateField.Input>
                            {(segment) => <DateField.Segment segment={segment} />}
                        </DateField.Input>
                        <DateField.Suffix>
                            <DatePicker.Trigger>
                                <DatePicker.TriggerIndicator />
                            </DatePicker.Trigger>
                        </DateField.Suffix>
                    </DateField.Group>
                    <DatePicker.Popover className="column-filter__date-popover">
                        <Calendar aria-label={`Дата: ${columnLabel}`}>
                            <Calendar.Header>
                                <Calendar.YearPickerTrigger>
                                    <Calendar.YearPickerTriggerHeading />
                                    <Calendar.YearPickerTriggerIndicator />
                                </Calendar.YearPickerTrigger>
                                <Calendar.NavButton slot="previous" />
                                <Calendar.NavButton slot="next" />
                            </Calendar.Header>
                            <Calendar.Grid>
                                <Calendar.GridHeader>
                                    {(day) => <Calendar.HeaderCell>{day}</Calendar.HeaderCell>}
                                </Calendar.GridHeader>
                                <Calendar.GridBody>
                                    {(date) => <Calendar.Cell date={date} />}
                                </Calendar.GridBody>
                            </Calendar.Grid>
                            <Calendar.YearPickerGrid>
                                <Calendar.YearPickerGridBody>
                                    {({ year }) => <Calendar.YearPickerCell year={year} />}
                                </Calendar.YearPickerGridBody>
                            </Calendar.YearPickerGrid>
                        </Calendar>
                    </DatePicker.Popover>
                </DatePicker>
            </div>
            <span className="column-filter__date-dash" aria-hidden="true">
                —
            </span>
            <div className="column-filter__date-row">
                <span className="column-filter__date-label" aria-hidden="true">
                    макс:
                </span>
                <DatePicker
                    className="column-filter__date-field"
                    aria-label={`Максимум: ${columnLabel}`}
                    value={toDateValue(value.max)}
                    onChange={(next) => onChange({ ...value, max: bound(next) })}
                    granularity="second"
                    hourCycle={24}
                    hideTimeZone={!SHOW_TIME_ZONE}
                    shouldForceLeadingZeros
                >
                    <DateField.Group fullWidth variant="secondary">
                        <DateField.Input>
                            {(segment) => <DateField.Segment segment={segment} />}
                        </DateField.Input>
                        <DateField.Suffix>
                            <DatePicker.Trigger>
                                <DatePicker.TriggerIndicator />
                            </DatePicker.Trigger>
                        </DateField.Suffix>
                    </DateField.Group>
                    <DatePicker.Popover className="column-filter__date-popover">
                        <Calendar aria-label={`Дата: ${columnLabel}`}>
                            <Calendar.Header>
                                <Calendar.YearPickerTrigger>
                                    <Calendar.YearPickerTriggerHeading />
                                    <Calendar.YearPickerTriggerIndicator />
                                </Calendar.YearPickerTrigger>
                                <Calendar.NavButton slot="previous" />
                                <Calendar.NavButton slot="next" />
                            </Calendar.Header>
                            <Calendar.Grid>
                                <Calendar.GridHeader>
                                    {(day) => <Calendar.HeaderCell>{day}</Calendar.HeaderCell>}
                                </Calendar.GridHeader>
                                <Calendar.GridBody>
                                    {(date) => <Calendar.Cell date={date} />}
                                </Calendar.GridBody>
                            </Calendar.Grid>
                            <Calendar.YearPickerGrid>
                                <Calendar.YearPickerGridBody>
                                    {({ year }) => <Calendar.YearPickerCell year={year} />}
                                </Calendar.YearPickerGridBody>
                            </Calendar.YearPickerGrid>
                        </Calendar>
                    </DatePicker.Popover>
                </DatePicker>
            </div>
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

/** The cell's text for a picker value — used by the popover's own hints. */
export function formatFilterDate(value: DateValue | null): string {
    return value === null ? "" : CELL_FORMAT.format(value.toDate(getLocalTimeZone()));
}
