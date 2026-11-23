import { expect, test } from "bun:test";

import { buildGridColumns } from "../grid-config";
import type { ColumnDescriptor, GridColumnContext, GridRow } from "../grid-config";

const context: GridColumnContext = {
    isAdmin: true,
    showAssetColumn: true,
    keycloakEnabled: false,
    readOnly: false,
    hasBaseline: false,
    assets: [],
    deliveryBases: [],
    selectedRowIds: new Set<string>(),
    showServiceColumns: false,
    remoteLocks: new Map(),
    remoteCellInfo: new Map(),
    onBooleanChange: () => {},
    descriptors: [
        { key: "name", label: "Название", width: 200, kind: "text" },
        { key: "total", label: "Итого", width: 120, kind: "calculated", fixed: true },
        { key: "market", label: "Рынок", width: 120, kind: "text", calculatedText: true },
    ] satisfies ColumnDescriptor[],
};

function classesFor(row: GridRow, prop: string): Record<string, boolean> {
    const columns = buildGridColumns("logisticsRates", context);
    const leaf = columns.find((column) => "prop" in column && String(column.prop) === prop) as
        | { cellProperties?: (props: { model: unknown }) => unknown }
        | undefined;
    if (!leaf?.cellProperties) throw new Error(`no cellProperties for ${prop}`);
    const result = leaf.cellProperties({ model: row }) as {
        className: Record<string, boolean>;
    };
    return result.className;
}

function row(overrides: Partial<GridRow> = {}): GridRow {
    return {
        id: "row-1",
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        ...overrides,
    };
}

test("a real row's computed cells carry the green calculated class", () => {
    expect(classesFor(row(), "total")["report-cell--calculated"]).toBe(true);
    expect(classesFor(row(), "market")["report-cell--calculated"]).toBe(true);
});

test("an ordinary add-row keeps its computed cells neutral", () => {
    const classes = classesFor(row({ isPlaceholder: true }), "total");
    expect(classes["report-cell--placeholder"]).toBe(true);
    expect(classes["report-cell--calculated"]).toBeFalsy();
});

test("a draft that previews computed cells is green in the add-row too", () => {
    const withPreview = row({
        isPlaceholder: true,
        highlightCalculatedInPlaceholder: true,
    });
    for (const prop of ["total", "market"]) {
        const classes = classesFor(withPreview, prop);
        expect(classes["report-cell--placeholder"]).toBe(true);
        expect(classes["report-cell--calculated"]).toBe(true);
        // The green tint must not be contradicted by the «empty cell» one.
        expect(classes["report-cell--empty"]).toBeFalsy();
    }
});

test("editable cells of a preview draft are NOT painted as calculated", () => {
    const classes = classesFor(
        row({ isPlaceholder: true, highlightCalculatedInPlaceholder: true }),
        "name",
    );
    expect(classes["report-cell--placeholder"]).toBe(true);
    expect(classes["report-cell--calculated"]).toBeFalsy();
});

test("a deleted row never carries the calculated highlight", () => {
    const classes = classesFor(row({ isDeleted: true }), "total");
    expect(classes["report-cell--calculated"]).toBeFalsy();
});
