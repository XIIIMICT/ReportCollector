"use client";

import type { FacetRange } from "@/shared/ui/column-filter-facets";

/**
 * The numeric half of a column filter's search: a MIN–MAX pair instead of a
 * text query.
 *
 * Typing «12» into a text search for a number column matches «12», «312»,
 * «1200»… — every value that merely contains those digits. A range is what a
 * numeric column actually gets filtered by, and it also survives the paging:
 * the popover asks the SOURCE for the whole matching set (see `FacetSearch`)
 * instead of filtering the loaded slice, which the text search can only do.
 *
 * Both bounds are optional and independent — only a minimum means «от него и
 * выше», only a maximum means «до него включительно». An empty box means «no
 * bound», which is NOT the same as zero.
 */
export const EMPTY_RANGE: FacetRange = { min: null, max: null };

/** Does the range impose no constraint at all? */
export function isRangeEmpty(range: FacetRange): boolean {
    return range.min === null && range.max === null;
}

/**
 * Two numeric boxes with a dash between them — the filter's search field for a
 * number column. The parent spaces them (flex, one gap), so the row reads as a
 * single control.
 *
 * The fields never commit on Enter: the popover's commit footer is the only
 * thing that applies a filter, and Enter there means «Применить».
 */
export function ColumnFilterRange({
    columnLabel,
    value,
    onChange,
}: {
    /** The column's header text, for the fields' accessible names. */
    columnLabel: string;
    value: FacetRange;
    onChange: (next: FacetRange) => void;
}) {
    // An empty box is `""` in the DOM and `null` in the state: «no bound»,
    // which is NOT the same as zero.
    const bound = (raw: string): number | null => {
        if (raw.trim() === "") return null;
        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed : null;
    };
    return (
        <div className="column-filter__range" role="group" aria-label={`Диапазон: ${columnLabel}`}>
            {/* NOT `NumberField`: its text input reports the typed value only
                through react-aria's change events, which never reached our
                `onChange` here (the field kept the text, the range stayed empty,
                and the list never narrowed). A plain controlled input carrying
                `type="number"` gives the same numeric semantics — the browser's
                own numeric keyboard and spinners — and commits on every
                keystroke. */}
            <input
                type="number"
                inputMode="decimal"
                aria-label={`Минимум: ${columnLabel}`}
                className="column-filter__range-field"
                placeholder="мин"
                value={value.min ?? ""}
                onChange={(event) => onChange({ ...value, min: bound(event.currentTarget.value) })}
            />
            <span className="column-filter__range-dash" aria-hidden="true">
                —
            </span>
            <input
                type="number"
                inputMode="decimal"
                aria-label={`Максимум: ${columnLabel}`}
                className="column-filter__range-field"
                placeholder="макс"
                value={value.max ?? ""}
                onChange={(event) => onChange({ ...value, max: bound(event.currentTarget.value) })}
            />
        </div>
    );
}
