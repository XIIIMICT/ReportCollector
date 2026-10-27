/**
 * The grid's operational callbacks every workspace writes by hand.
 *
 * `<DataGrid>` takes four toast handlers (`onValidationError`,
 * `onPasteSkipped`, `onPasteTruncated`, `onLockedCellEdit`) that were pasted
 * into five call sites. The copies had already drifted: the lock message says
 * «занимает» in the dictionaries and «редактирует» in the forms, and two of the
 * paste-truncation strings differed in wording.
 *
 * The handlers are pure UI reporting — no project state — so they belong here,
 * not in each workspace. `lockedVerb` keeps the one legitimate difference
 * explicit instead of letting it drift into a sixth copy.
 */

import { toast } from "@heroui/react";

import { PASTE_TRUNCATED_MESSAGE, lockedCellMessage } from "@/shared/ui/text-helpers";

export type GridWarningHandlers = {
    onValidationError: (message: string) => void;
    onPasteSkipped: (description: string) => void;
    onPasteTruncated: () => void;
    onLockedCellEdit: (key: string, ownerName: string) => void;
};

/**
 * The standard toast set for a grid.
 *
 * «занимает» — the dictionaries (a selection with an open lease is not an
 * edit); «редактирует» — the forms, where a lease means an active edit.
 */
export function gridWarningHandlers({
    lockedVerb = "занимает",
}: {
    lockedVerb?: "занимает" | "редактирует";
} = {}): GridWarningHandlers {
    return {
        onValidationError: (message) =>
            toast.warning("Некорректное значение", { description: message }),
        onPasteSkipped: (description) =>
            toast.warning("Вставка выполнена не полностью", { description }),
        onPasteTruncated: () =>
            toast.warning("Вставка обрезана", { description: PASTE_TRUNCATED_MESSAGE }),
        onLockedCellEdit: (_key, ownerName) =>
            toast.warning("Ячейка занята", {
                description: lockedCellMessage(ownerName, lockedVerb),
            }),
    };
}
