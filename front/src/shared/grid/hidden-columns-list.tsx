"use client";

/**
 * One hidden column as every list shows it: its number IN THE TABLE (not the
 * list index) and its header chain, group first.
 */
export type HiddenColumnEntry = {
    /** The table's column number (plan workbook ordinal / grid slot), null when unnumbered. */
    number: number | null;
    /** Header chain, group first, leaf last. */
    path: string[];
};

/** The numbered hidden-column list shared by the «+» tooltip and the status tooltip. */
export function HiddenColumnsList({
    entries,
    className,
}: {
    entries: readonly HiddenColumnEntry[];
    className?: string;
}) {
    if (entries.length === 0) return null;
    return (
        <ul className={["grid-hidden-columns", className].filter(Boolean).join(" ")}>
            {entries.map((entry, index) => (
                <li key={`${entry.number ?? "x"}-${entry.path.join(">")}-${index}`}>
                    <strong>{entry.number ?? "—"}.</strong> {entry.path.join(" → ")}
                </li>
            ))}
        </ul>
    );
}
