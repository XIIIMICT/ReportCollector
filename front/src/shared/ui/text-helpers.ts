/**
 * Small helpers every workspace repeats.
 *
 * They lived (byte-identically) in all three feature folders; a change to any of
 * them — a default message, the candidate order of a record label — had to be
 * made three times, and one of the copies always drifted.
 */

import { ReportCollectorApiError } from "@/shared/api/api-error";
import type { GridRow } from "@/shared/grid/grid-config";

/** Wrap an API row into the grid's row envelope (nothing changed yet). */
export function toGridRow<T extends { id: string }>(row: T): GridRow {
    return {
        ...row,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: false,
    };
}

/** A trimmed string, or an empty one (the placeholder-cell value). */
export function trimmedText(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** First non-empty of `keys` on the row — the label a delete dialog shows. */
export function recordLabel(
    row: object | undefined,
    keys: readonly string[] = ["name", "fullname", "email"],
): string | null {
    if (!row) return null;
    const record = row as Record<string, unknown>;
    const value = keys
        .map((key) => record[key])
        .find((candidate) => typeof candidate === "string" && candidate.trim());
    return typeof value === "string" ? value : null;
}

/**
 * The human message of a thrown API/UI error. A backend request id is appended
 * as an opaque token so a toast can be correlated with `X-Request-ID` logs;
 * network failures never contribute their raw (possibly URL-bearing) text.
 */
export function errorMessage(reason: unknown): string {
    const base = reason instanceof Error ? reason.message : "Неизвестная ошибка.";
    const requestId = reason instanceof ReportCollectorApiError ? reason.requestId : undefined;
    return requestId ? `${base} (ID запроса: ${requestId})` : base;
}

/**
 * The toast text a grid raises when a paste buffer is bigger than the
 * selection. One string, because three grids and five call sites said the same
 * thing in slightly different words.
 */
export const PASTE_TRUNCATED_MESSAGE =
    "Вставляемый диапазон больше выделенного — значения вставлены только в выделенные ячейки.";

/**
 * The lock message. `verb` is the project's wording: the dictionaries say
 * «занимает» (a cell may be selected, not edited), the forms say
 * «редактирует» (an active edit).
 */
export function lockedCellMessage(
    ownerName: string,
    verb: "занимает" | "редактирует" = "занимает",
): string {
    return `Сейчас её ${verb} ${ownerName}.`;
}

/** The copy/paste actions the action bar advertises for a grid. */
export type ClipboardActions = {
    canPaste: boolean;
    onCopy: () => void;
    onPaste: () => void;
};

/** The minimal grid handle the adapter needs. */
type ClipboardGridHandle = {
    copySelection: () => unknown;
    pasteIntoSelection: (text?: string) => Promise<unknown> | unknown;
};

/**
 * Adapt a grid's clipboard callback into the action-bar state.
 *
 * The grid reports only `canPaste` (the browser hides clipboard contents from
 * us); copy/paste are wired to the handle. Pasted byte-identically in four
 * workspaces, differing only in where the result was stored.
 */
export function clipboardActionsFrom(
    state: { canPaste: boolean },
    handle: ClipboardGridHandle | null | undefined,
): ClipboardActions {
    return {
        canPaste: state.canPaste,
        onCopy: () => void handle?.copySelection(),
        onPaste: () => void handle?.pasteIntoSelection(),
    };
}

/**
 * Ignore equal publications, but accept new handlers when the active grid
 * changes even if both grids report the same paste availability.
 */
export function updateClipboardActions(
    setState: (update: (current: ClipboardActions) => ClipboardActions) => void,
    next: ClipboardActions,
): void {
    setState((current) =>
        current.canPaste === next.canPaste &&
        current.onCopy === next.onCopy &&
        current.onPaste === next.onPaste
            ? current
            : next,
    );
}

/** The argument `onSelectionChange` receives from a grid. */
export type GridSelectionInfo = {
    label: string | null | undefined;
    cellKeys: string[];
    detail?: string | null;
};

/** The room side of selection publishing. */
type SelectionRoom = {
    setSelection: (selection: { label: string; cellKeys: string[] } | null) => void;
};

/**
 * Publish a grid selection into the collaboration room and the footer label.
 *
 * Every workspace did this inline (twice verbatim, once with the
 * preemption-suppression branch). `suppressNextClear` is the «Сырьё» case: the
 * clear that follows a preemption from another tab of the same account must
 * not be sent — the server entry belongs to the other tab.
 */
export function publishSelection({
    selection,
    room,
    onLabel,
    onDetail,
    suppressNextClear,
}: {
    selection: GridSelectionInfo;
    room: SelectionRoom;
    onLabel: (label: string | null) => void;
    onDetail?: ((detail: string | null) => void) | undefined;
    suppressNextClear?: { current: boolean } | undefined;
}): void {
    const { label, cellKeys, detail } = selection;
    const hasLabel = typeof label === "string" && label.length > 0;
    onLabel(hasLabel ? label : null);
    onDetail?.(hasLabel ? (detail ?? null) : null);
    if (suppressNextClear?.current) {
        suppressNextClear.current = false;
        if (!hasLabel) return;
    }
    room.setSelection(hasLabel ? { label, cellKeys } : null);
}
