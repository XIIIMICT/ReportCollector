/**
 * Grid clipboard utilities for copy/paste operations
 */

/**
 * Serialize a cell value to string for clipboard export
 */
export function serializeCellValue(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(serializeCellValue).join(", ");
    if (typeof value === "object") {
        const named = value as { name?: unknown };
        return named.name !== undefined ? String(named.name) : String(value);
    }
    return String(value);
}

/**
 * Parse pasted text into 2D array, trimming trailing empty rows and columns
 */
export function parsePastedText(text: string): string[][] {
    // Excel and other apps put trailing line breaks and tabs
    const split = text.split(/\r\n|\n|\r/).map((row) => row.split("\t"));

    // Trim trailing empty rows
    while (split.length > 0 && split[split.length - 1]?.every((cell) => cell.trim() === "")) {
        split.pop();
    }

    if (split.length === 0) return [];

    // Find actual width (trim trailing empty columns)
    let width = Math.max(...split.map((cells) => cells.length));
    while (
        width > 0 &&
        split.every((cells) => {
            const cell = cells[width - 1];
            return width - 1 >= cells.length || (cell !== undefined && cell.trim() === "");
        })
    ) {
        width -= 1;
    }

    return split.map((cells) => cells.slice(0, width));
}

/**
 * Build TSV text from rows and columns
 */
export function buildTSV<TRow extends Record<string, unknown>>(
    rows: TRow[],
    props: string[],
): string {
    const lines = rows.map((row) => props.map((prop) => serializeCellValue(row[prop])).join("\t"));
    return lines.join("\n");
}

/**
 * Copy text to clipboard with fallback to textarea for unsupported browsers
 */
export async function copyToClipboard(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        try {
            const textarea = document.createElement("textarea");
            textarea.value = text;
            textarea.style.position = "fixed";
            textarea.style.opacity = "0";
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand("copy");
            textarea.remove();
            return true;
        } catch {
            return false;
        }
    }
}

/**
 * Read text from clipboard
 */
export async function readFromClipboard(): Promise<string | null> {
    try {
        return await navigator.clipboard.readText();
    } catch {
        return null;
    }
}
