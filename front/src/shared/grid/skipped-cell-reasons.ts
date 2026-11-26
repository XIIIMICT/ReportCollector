/**
 * Build the Russian description of cells skipped during a range paste.
 *
 * Both reasons and the labels under each reason keep first-seen order; labels
 * are quoted with «» and joined with ", ", reason groups with "; ". An empty
 * iterable yields an empty string (callers guard the `size > 0` case).
 */
export function groupSkippedCells(entries: Iterable<readonly [string, string]>): string {
    const reasons = new Map<string, string[]>();
    for (const [label, reason] of entries) {
        const labels = reasons.get(reason) ?? [];
        labels.push(label);
        reasons.set(reason, labels);
    }
    return [...reasons.entries()]
        .map(([reason, labels]) => `${labels.map((label) => `«${label}»`).join(", ")}: ${reason}`)
        .join("; ");
}
