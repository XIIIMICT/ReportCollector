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
const h: Hyper = (tag, props, ...rest) => {
    // RevoGrid's `h` takes the text (or the children) as its 3rd argument.
    const children = rest.length > 1 ? (rest as (string | VNode)[]) : rest[0];
    return { t: tag, p: props, c: children as string | (string | VNode)[] | undefined };
};

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
            return template === undefined
                ? undefined
                : template(h, { name: String(leaf?.name ?? "") });
        },
    };
}

test("the header renders the label AND our funnel button", () => {
    // A custom `columnTemplate` REPLACES the header text, so it draws the label
    // itself; the funnel is ours (the app's button), never RevoGrid's — the grid
    // opts its own filter UI out with `filter: false`.
    const { leaf, render } = templateOf("renovationAssets", "name");
    expect(leaf?.name).toBe("Наименование");
    const node = render();
    const children = node?.c as Array<string | { p: Record<string, unknown> }>;
    expect(children[0]).toBe("Наименование");
    expect(children[1]?.p.class).toBe("header-filter-button");
    expect(children[1]?.p["data-prop"]).toBe("name");
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
