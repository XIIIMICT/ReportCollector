import { describe, expect, test } from "bun:test";

import { buildExportAction, NOTHING_TO_EXPORT_REASON } from "../action-items";

/**
 * The export button follows the shared rule: an empty table has nothing to
 * download, so the button is disabled and explains itself — in every project,
 * through the one builder they all use.
 */
describe("buildExportAction", () => {
    const disabledFor = (rowCount: number | undefined, disabled = false) =>
        buildExportAction({ pending: false, disabled, rowCount, onExport: () => {} }).disabled;

    test("a table with rows keeps the export enabled", () => {
        expect(disabledFor(12)).toBe(false);
    });

    test("an empty table disables the export", () => {
        expect(disabledFor(0)).toBe(true);
    });

    test("an unknown row count does not disable the export", () => {
        expect(disabledFor(undefined)).toBe(false);
    });

    test("an empty table shows the reason on the disabled button", () => {
        const action = buildExportAction({
            pending: false,
            disabled: false,
            rowCount: 0,
            onExport: () => {},
        });
        expect(action.disabledReason).toBe(NOTHING_TO_EXPORT_REASON);
    });

    test("another disabled reason wins over the empty-table hint", () => {
        const action = buildExportAction({
            pending: true,
            disabled: true,
            rowCount: 0,
            onExport: () => {},
        });
        expect(action.disabledReason).toBeNull();
    });

    test("a non-empty table carries no reason", () => {
        const action = buildExportAction({
            pending: false,
            disabled: false,
            rowCount: 3,
            onExport: () => {},
        });
        expect(action.disabledReason).toBeNull();
    });
});
