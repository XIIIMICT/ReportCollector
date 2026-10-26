"use client";

import { Calendar, DateField, DatePicker, TimeField, type TimeValue } from "@heroui/react";
import {
    type CalendarDate,
    type CalendarDateTime,
    DateFormatter,
    type DateValue,
    getLocalTimeZone,
    parseAbsoluteToLocal,
    Time,
    toCalendarDate,
    toCalendarDateTime,
} from "@internationalized/date";

import type { FacetDateRange } from "@/shared/ui/column-filter-facets";

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

/** Show the picker's time-zone suffix? Kept as a flag, off for now. */
export const SHOW_TIME_ZONE = false;

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
export function toDateValue(
    epochMs: number | null,
    kind: DateFilterKind = "datetime",
): CalendarDateTime | CalendarDate | null {
    if (epochMs === null) return null;
    const parsed = parseAbsoluteToLocal(new Date(epochMs).toISOString());
    return kind === "date" ? toCalendarDate(parsed) : toCalendarDateTime(parsed);
}

/**
 * Convert a picker value back into the stored epoch-ms bound.
 *
 * A cleared picker yields `null` («no bound»), not `NaN` — the filter treats a
 * missing bound as «unconstrained», and every comparison against `NaN` is false,
 * which would silently empty the table. For a date the local midnight of the day
 * is the bound, matching how the cell's text is parsed back.
 */
export function fromDateValue(next: DateValue | null): number | null {
    return next === null ? null : next.toDate(getLocalTimeZone()).getTime();
}

/**
 * The calendar popover both pickers share: month navigation plus a year picker
 * for jumping decades back.
 *
 * For a TIMESTAMP column it also carries a «Время» row: the field's own time
 * segments are fiddly to hit, and the picker's calendar is where a user already
 * is when they pick the day. The row edits the SAME value — the picker's state
 * drives it — so the two stay in step whichever one is touched. A date-only
 * column has no time to edit and gets no row.
 */
function PickerCalendar({ columnLabel }: { columnLabel: string }) {
    return (
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
                <Calendar.GridBody>{(date) => <Calendar.Cell date={date} />}</Calendar.GridBody>
            </Calendar.Grid>
            <Calendar.YearPickerGrid>
                <Calendar.YearPickerGridBody>
                    {({ year }) => <Calendar.YearPickerCell year={year} />}
                </Calendar.YearPickerGridBody>
            </Calendar.YearPickerGrid>
            {/* NOT inside the Calendar: it is a fixed-row grid, and an extra
                child pushes its cells out of the popover so that a day click
                lands on the underlay and dismisses everything. The time row is
                rendered by the picker's popover, right below the calendar. */}
        </Calendar>
    );
}

/**
 * The «Время:» row under a timestamp picker's calendar.
 *
 * It edits the SAME bound the field holds: a time is only meaningful on a day,
 * so an empty bound keeps no time, and setting one turns the bound into that day
 * at that time — which is exactly what the field then shows.
 */
function DateTimeRow({
    columnLabel,
    value,
    onChange,
}: {
    columnLabel: string;
    value: number | null;
    onChange: (next: number | null) => void;
}) {
    const current = value === null ? null : toDateValue(value, "datetime");
    // A `Time` from the date package, not a bare object: `TimeField` compares
    // values by their calendar fields, and a plain literal fails that.
    const timeValue: TimeValue | null =
        current !== null && "hour" in current
            ? new Time(current.hour, current.minute, current.second)
            : null;
    return (
        <div className="column-filter__date-time">
            <span className="column-filter__date-label">Время:</span>
            <TimeField
                aria-label={`Время: ${columnLabel}`}
                className="column-filter__date-time-field"
                granularity="second"
                hourCycle={24}
                hideTimeZone={!SHOW_TIME_ZONE}
                shouldForceLeadingZeros
                value={timeValue}
                onChange={(next) => {
                    if (next === null || current === null) return;
                    const dateTime = toCalendarDateTime(current).set({
                        hour: next.hour,
                        minute: next.minute,
                        second: next.second ?? 0,
                    });
                    onChange(fromDateValue(dateTime));
                }}
            >
                <TimeField.Group variant="secondary">
                    <TimeField.Input>
                        {(segment) => <TimeField.Segment segment={segment} />}
                    </TimeField.Input>
                </TimeField.Group>
            </TimeField>
        </div>
    );
}

/**
 * Two pickers with a dash between them — the filter's search field for a date or
 * timestamp column. The parent lays them out ONE ABOVE THE OTHER, so the pair
 * reads as a single control.
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
    const bound = fromDateValue;
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
                <div className="column-filter__date-row" key={slot}>
                    <span className="column-filter__date-label" aria-hidden="true">
                        {label}:
                    </span>
                    <DatePicker
                        className="column-filter__date-field"
                        aria-label={`${text}: ${columnLabel}`}
                        value={toDateValue(value[slot], kind)}
                        onChange={(next) => onChange({ ...value, [slot]: bound(next) })}
                        granularity={kind === "date" ? "day" : "second"}
                        {...(kind === "datetime" ? { hourCycle: 24 as const } : {})}
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
                        {/* Anchored to the FIELD, inside the filter's popover:
                            `containerPadding`/`shouldFlip` keep it from escaping
                            the viewport, where a click would land on the page
                            background and dismiss everything. */}
                        {/* The calendar lives in the document portal, right next
                            to the filter panel. Two things kept it from being
                            clicked: react-aria gives both popovers the same
                            `z-index` (so the calendar slipped UNDER the panel),
                            and the portal asks for `display: contents` — the
                            node then has no box of its own and the hit test
                            resolves to the page background. Both are inline
                            styles, hence here rather than in CSS. */}
                        <DatePicker.Popover
                            className="column-filter__date-popover"
                            style={{ zIndex: 100001, display: "block" }}
                            placement="bottom start"
                            offset={4}
                            containerPadding={8}
                            shouldFlip
                        >
                            <PickerCalendar columnLabel={columnLabel} />
                            {kind === "datetime" ? (
                                <DateTimeRow
                                    columnLabel={columnLabel}
                                    value={value[slot]}
                                    onChange={(next) => onChange({ ...value, [slot]: next })}
                                />
                            ) : null}
                        </DatePicker.Popover>
                    </DatePicker>
                </div>
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
