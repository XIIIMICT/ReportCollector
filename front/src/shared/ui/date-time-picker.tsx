"use client";

import { Calendar, DateField, DatePicker, TimeField, type TimeValue } from "@heroui/react";
import {
    type CalendarDate,
    type CalendarDateTime,
    type DateValue,
    getLocalTimeZone,
    parseAbsoluteToLocal,
    Time,
    toCalendarDate,
    toCalendarDateTime,
} from "@internationalized/date";

/** `date` — a calendar DAY; `datetime` — an instant with a time. */
export type DatePickerKind = "date" | "datetime";

/** Show the picker's time-zone suffix? Kept as a flag, off for now. */
export const DATE_PICKER_SHOW_TIME_ZONE = false;

/**
 * THE date picker of the app: a field with a calendar popover, and — for a
 * timestamp — a «Время:» row inside that popover.
 *
 * One component for every place that needs a date or a date-time, because the
 * calendar carries four fixes that are easy to lose when it is re-implemented:
 *
 * 1. the popover is anchored to the FIELD (`bottom start`, flip, container
 *    padding) instead of escaping to the viewport edge;
 * 2. its `z-index` is set INLINE, one step above any host popover — react-aria
 *    gives every portal the same value, so a calendar inside a filter panel
 *    otherwise slips UNDER that panel;
 * 3. it is `display: block` for the same reason: the portal wrapper asks for
 *    `display: contents`, the node then has no box of its own, and the hit test
 *    resolves to the page background — every click dismissed everything;
 * 4. the time row lives in the POPOVER, never inside `Calendar`: that is a
 *    fixed-row grid, and an extra child pushes its cells out of the popover.
 *
 * The time row edits the SAME value the field holds, so the two agree no matter
 * which one is touched. A date-only picker gets no row.
 */
export function DateTimePicker({
    label,
    ariaLabel,
    value,
    onChange,
    kind = "datetime",
    className,
    fieldClassName,
}: {
    /** Caption shown to the LEFT of the field, without the colon. */
    label?: string;
    /** Accessible name of the field (and the base of its inner labels). */
    ariaLabel: string;
    /** The value as epoch ms — the app's one representation of an instant. */
    value: number | null;
    onChange: (next: number | null) => void;
    kind?: DatePickerKind;
    className?: string;
    fieldClassName?: string;
}) {
    const current = toPickerValue(value, kind);
    const withTime = kind === "datetime";
    return (
        <div className={["date-time-picker", className].filter(Boolean).join(" ")}>
            {label !== undefined ? (
                <span className="date-time-picker__label" aria-hidden="true">
                    {label}:
                </span>
            ) : null}
            <DatePicker
                className={["date-time-picker__field", fieldClassName].filter(Boolean).join(" ")}
                aria-label={ariaLabel}
                value={current}
                onChange={(next) => onChange(fromPickerValue(next))}
                granularity={withTime ? "second" : "day"}
                {...(withTime ? { hourCycle: 24 as const } : {})}
                hideTimeZone={!DATE_PICKER_SHOW_TIME_ZONE}
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
                <DatePicker.Popover
                    className="date-time-picker__popover"
                    style={{ zIndex: 100001 }}
                    placement="bottom start"
                    offset={4}
                    containerPadding={8}
                    shouldFlip
                >
                    <PickerCalendar columnLabel={ariaLabel} />
                    {withTime ? (
                        <TimeRow
                            label={ariaLabel}
                            value={current}
                            onChange={(next) => onChange(fromPickerValue(next))}
                        />
                    ) : null}
                </DatePicker.Popover>
            </DatePicker>
        </div>
    );
}

/**
 * Month navigation, a year picker for jumping decades back, and the day grid.
 * Nothing else: see the note on `DateTimePicker` about extra children.
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
        </Calendar>
    );
}

/**
 * The «Время:» row under the calendar. It edits the day the picker already
 * points at, so the bound becomes «that day, that time» — which is what the
 * field then shows.
 */
function TimeRow({
    label,
    value,
    onChange,
}: {
    label: string;
    value: DateValue | null;
    onChange: (next: DateValue | null) => void;
}) {
    // A `Time` from the date package, not a bare object: `TimeField` compares
    // values by their calendar fields, and a plain literal fails that.
    const time: TimeValue | null =
        value !== null && "hour" in value ? new Time(value.hour, value.minute, value.second) : null;
    return (
        <div className="date-time-picker__time">
            <span className="date-time-picker__label">Время:</span>
            <TimeField
                aria-label={`Время: ${label}`}
                className="date-time-picker__time-field"
                granularity="second"
                hourCycle={24}
                hideTimeZone={!DATE_PICKER_SHOW_TIME_ZONE}
                shouldForceLeadingZeros
                value={time}
                onChange={(next) => {
                    if (next === null || value === null) return;
                    onChange(
                        toCalendarDateTime(value).set({
                            hour: next.hour,
                            minute: next.minute,
                            second: next.second ?? 0,
                        }),
                    );
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
 * The range the picker's calendar can represent (a `@internationalized/date`
 * limit): an instant outside it throws «Value out of range».
 */
const PICKER_MIN_MS = Date.UTC(1, 0, 1);
const PICKER_MAX_MS = Date.UTC(9999, 11, 31, 23, 59);

/** Is this epoch inside the picker's representable range? */
export function isPickerRange(epochMs: number | null): epochMs is number {
    return (
        epochMs !== null &&
        Number.isFinite(epochMs) &&
        epochMs >= PICKER_MIN_MS &&
        epochMs <= PICKER_MAX_MS
    );
}

/**
 * Epoch ms → the picker's calendar value; a date keeps the day only.
 *
 * An instant outside the calendar's range is NOT a value to show: a one-sided
 * window used to be widened to the far ends of time (`new Date(8.64e15)`), and
 * rendering that threw «Value out of range: -9999 <= 275760 <= 9999» and took the
 * whole form down. Such a bound simply reads as «не задано».
 */
export function toPickerValue(
    epochMs: number | null,
    kind: DatePickerKind = "datetime",
): CalendarDateTime | CalendarDate | null {
    if (!isPickerRange(epochMs)) return null;
    const parsed = parseAbsoluteToLocal(new Date(epochMs).toISOString());
    return kind === "date" ? toCalendarDate(parsed) : toCalendarDateTime(parsed);
}

/**
 * The picker's value → epoch ms.
 *
 * A cleared picker is `null` («no bound»), never `NaN`: every comparison against
 * `NaN` is false, which would silently empty a table. For a date the LOCAL
 * MIDNIGHT of the chosen day is the instant — the same one the cells' text parses
 * back to, so a filter and a cell agree on what «03.02.2026» means.
 */
export function fromPickerValue(next: DateValue | null): number | null {
    return next === null ? null : next.toDate(getLocalTimeZone()).getTime();
}
