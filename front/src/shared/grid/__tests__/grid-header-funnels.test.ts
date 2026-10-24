import { expect, test } from "bun:test";

import { buildGridColumns, getVisibleDescriptors, markFilterableColumns } from "../grid-config";
import type { GridColumnContext } from "../grid-config";

const context: GridColumnContext = {
    isAdmin: true,
    showAssetColumn: true,
    keycloakEnabled: false,
    readOnly: false,
    hasBaseline: false,
    assets: [],
    deliveryBases: [],
    selectedRowIds: new Set<string>(),
    showServiceColumns: true,
    remoteLocks: new Map(),
    remoteCellInfo: new Map(),
    onBooleanChange: () => {},
};

test("every filterable leaf of the assets dictionary carries its own template", () => {
    // The dictionary's own columns AND the service columns («Создан»,
    // «Обновлён», «Изменил») are filterable — only the computed aggregates
    // («Число ремонтных работ», «Стоимость итого») are not.
    const descriptors = getVisibleDescriptors("renovationAssets", true, true);
    const columns = buildGridColumns("renovationAssets", context);
    markFilterableColumns(columns, descriptors);
    const leaves = columns
        .flatMap((column) =>
            "children" in column && column.children?.length
                ? (column.children as unknown as { prop: string; columnTemplate?: unknown }[])
                : [column as unknown as { prop: string; columnTemplate?: unknown }],
        )
        .filter((leaf) => leaf.prop !== undefined);
    const templated = leaves
        .filter((leaf) => typeof leaf.columnTemplate === "function")
        .map((leaf) => String(leaf.prop));
    // Each leaf gets its OWN function: `markFilterableColumns` must not hand
    // the same reference to every column.
    const referenced = new Set(
        leaves
            .filter((leaf) => typeof leaf.columnTemplate === "function")
            .map((leaf) => leaf.columnTemplate),
    );
    expect(templated).toEqual(["name", "repairCode", "createdAt", "updatedAt", "updatedBy"]);
    expect(referenced.size).toBe(templated.length);
    expect(leaves.find((leaf) => leaf.prop === "repairCount")?.columnTemplate).toBeUndefined();
});
