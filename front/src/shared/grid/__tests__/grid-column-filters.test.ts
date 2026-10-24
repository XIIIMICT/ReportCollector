import { expect, test } from "bun:test";

import {
    buildGridColumns,
    facetTextOf,
    getVisibleDescriptors,
    markFilterableColumns,
    type ColumnDescriptor,
    type GridColumnContext,
} from "../grid-config";

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
};

type Leaf = { prop: string | number; columnTemplate?: unknown };

function leaves(columns: ReturnType<typeof buildGridColumns>): Leaf[] {
    return columns.flatMap((column) =>
        "children" in column && column.children?.length
            ? (column.children as unknown as Leaf[])
            : [column as unknown as Leaf],
    );
}

test("every data column of a dictionary gets a header template", () => {
    // No `filter` prop reaches RevoGrid any more (its condition panel is gone),
    // so `columnTemplate` is the ONLY trigger for the built-in `.rv-filter`
    // button. Without it the headers render with no funnel at all.
    const descriptors = getVisibleDescriptors("renovationAssets", true, false);
    const columns = buildGridColumns("renovationAssets", context);
    markFilterableColumns(columns, descriptors);
    const byProp = new Map(leaves(columns).map((leaf) => [String(leaf.prop), leaf]));
    expect(typeof byProp.get("name")?.columnTemplate).toBe("function");
    expect(typeof byProp.get("repairCode")?.columnTemplate).toBe("function");
});

test("a computed column carries no funnel", () => {
    // «Число ремонтных работ» / «Стоимость итого» are server-side aggregates:
    // filtering them would filter a number the table never stores.
    const descriptors = getVisibleDescriptors("renovationAssets", true, false);
    const columns = buildGridColumns("renovationAssets", context);
    markFilterableColumns(columns, descriptors);
    const byProp = new Map(leaves(columns).map((leaf) => [String(leaf.prop), leaf]));
    expect(byProp.get("repairCount")?.columnTemplate).toBeUndefined();
    expect(byProp.get("totalCost")?.columnTemplate).toBeUndefined();
});

test("a column is filtered by the text its cell shows", () => {
    const select: ColumnDescriptor = {
        key: "status",
        label: "Статус",
        width: 120,
        kind: "select",
        options: [
            { value: "ok", label: "Готово" },
            { value: "wait", label: "В работе" },
        ],
    };
    const grouped: ColumnDescriptor = {
        key: "dzoNames",
        label: "ДЗО",
        width: 200,
        kind: "assetsMulti",
        displayKey: "dzoDisplay",
    };
    expect(facetTextOf(select, { status: "ok" })).toBe("Готово");
    // An unknown code is still rendered as itself, so it stays filterable.
    expect(facetTextOf(select, { status: "legacy" })).toBe("legacy");
    expect(facetTextOf(grouped, { dzoNames: ["a"], dzoDisplay: "АО «А»" })).toBe("АО «А»");
    expect(facetTextOf(grouped, { dzoNames: ["a"] })).toEqual(["a"]);
});
