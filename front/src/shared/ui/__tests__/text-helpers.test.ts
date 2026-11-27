import { describe, expect, test } from "bun:test";

import { ReportCollectorApiError } from "@/shared/api/api-error";
import {
    clipboardActionsFrom,
    errorMessage,
    lockedCellMessage,
    recordLabel,
    toGridRow,
    trimmedText,
    updateClipboardActions,
} from "@/shared/ui/text-helpers";

describe("shared text and grid adapters", () => {
    test("recordLabel uses the first non-empty candidate", () => {
        expect(recordLabel({ name: "", fullname: "Иванов", email: "a@example.ru" })).toBe("Иванов");
    });

    test("trimmedText and lockedCellMessage keep UI wording centralized", () => {
        expect(trimmedText("  значение ")).toBe("значение");
        expect(lockedCellMessage("Иванов", "редактирует")).toBe("Сейчас её редактирует Иванов.");
    });

    test("toGridRow applies the single grid envelope without mutating the source", () => {
        const row = { id: "r1", name: "Сырьё" };
        const gridRow = toGridRow(row);
        expect(gridRow).toEqual({
            ...row,
            rowState: "unchanged",
            changedFields: [],
            isDeleted: false,
            isPlaceholder: false,
        });
        expect(row).toEqual({ id: "r1", name: "Сырьё" });
    });

    test("recordLabel honours the caller's candidate order (KPPK: name then code)", () => {
        expect(recordLabel({ code: "C-1", name: "  " }, ["name", "code"])).toBe("C-1");
        expect(recordLabel(undefined, ["name", "code"])).toBeNull();
    });

    test("errorMessage keeps the default Russian fallback for non-Error reasons", () => {
        expect(errorMessage(new Error("boom"))).toBe("boom");
        expect(errorMessage("boom")).toBe("Неизвестная ошибка.");
    });

    test("errorMessage appends an opaque request id without echoing other payload data", () => {
        expect(errorMessage(new ReportCollectorApiError("Нет доступа.", 403, "request-42"))).toBe(
            "Нет доступа. (ID запроса: request-42)",
        );
    });

    test("clipboard actions preserve the paste flag and call the grid handle", () => {
        let copies = 0;
        let pastes = 0;
        const actions = clipboardActionsFrom(
            { canPaste: true },
            { copySelection: () => copies++, pasteIntoSelection: () => pastes++ },
        );
        actions.onCopy();
        actions.onPaste();
        expect({ copies, pastes, canPaste: actions.canPaste }).toEqual({
            copies: 1,
            pastes: 1,
            canPaste: true,
        });
    });

    test("clipboard state does not replace equal state", () => {
        const current = { canPaste: false, onCopy: () => {}, onPaste: () => {} };
        let result = current;
        updateClipboardActions(
            (update) => {
                result = update(result);
            },
            { ...current },
        );
        expect(result).toBe(current);
    });

    test("switching grids replaces actions even when paste availability is equal", () => {
        const current = { canPaste: false, onCopy: () => {}, onPaste: () => {} };
        const next = { canPaste: false, onCopy: () => {}, onPaste: () => {} };
        let result = current;
        updateClipboardActions((update) => {
            result = update(result);
        }, next);
        expect(result).toBe(next);
    });
});
