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
    showServiceColumns: false,
    remoteLocks: new Map(),
    remoteCellInfo: new Map(),
    onBooleanChange: () => {},
};

type VNode = {
    t: string;
    p: Record<string, unknown>;
    c?: (VNode | string)[] | string | undefined;
};
type Hyper = (
    tag: string,
    props: Record<string, unknown>,
    children?: (VNode | string)[] | string,
) => VNode;

/** Minimal stand-in for RevoGrid's `h`, enough to read what a template renders. */
const h: Hyper = (tag, props, children) => ({ t: tag, p: props, c: children });

function templateOf(section: Parameters<typeof buildGridColumns>[0], prop: string) {
    const descriptors = getVisibleDescriptors(section, true, false);
    const columns = buildGridColumns(section, context);
    markFilterableColumns(columns, descriptors);
    const leaves = columns.flatMap((column) =>
        "children" in column && column.children?.length
            ? (column.children as unknown as {
                  prop: string;
                  name?: string;
                  columnTemplate?: unknown;
              }[])
            : [column as unknown as { prop: string; name?: string; columnTemplate?: unknown }],
    );
    const leaf = leaves.find((candidate) => String(candidate.prop) === prop);
    return {
        leaf,
        // Returns `undefined` when the column has no template at all (a computed
        // column), so a test can assert both "renders the name" and "no funnel".
        render: () => {
            const template = leaf?.columnTemplate as
                | ((hyper: Hyper, column: { name?: string | undefined }) => VNode)
                | undefined;
            return template === undefined ? undefined : template(h, { name: leaf?.name });
        },
    };
}

test("the header template renders the column name, not an empty cell", () => {
    // The header funnel is RevoGrid's `.rv-filter`, which renders only for a
    // column carrying `columnTemplate`. That template REPLACES the header text,
    // so it has to draw the column name itself — an empty template blanked every
    // dictionary header.
    const { leaf, render } = templateOf("renovationAssets", "name");
    const node = render();
    expect(leaf?.name).toBe("Наименование");
    const children = node?.c as unknown[];
    expect(children?.[0]).toBe("Наименование");
});

test("a computed column is filterable like any other", () => {
    // A computed cell holds a real per-row number, so «Число ремонтных работ»
    // filters by its values — it is only not EDITABLE.
    const descriptors = getVisibleDescriptors("renovationAssets", true, false);
    const columns = buildGridColumns("renovationAssets", context);
    markFilterableColumns(columns, descriptors);
    const leaves = columns.flatMap((column) =>
        "children" in column && column.children?.length
            ? (column.children as unknown as { prop: string; columnTemplate?: unknown }[])
            : [column as unknown as { prop: string; columnTemplate?: unknown }],
    );
    const byProp = new Map(leaves.map((leaf) => [String(leaf.prop), leaf]));
    for (const prop of ["name", "repairCode", "repairCount", "totalCost"]) {
        expect(byProp.get(prop)?.columnTemplate).toBeFunction();
    }
});
