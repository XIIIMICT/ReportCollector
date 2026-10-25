import { expect, test } from "bun:test";

import {
    buildGridColumns,
    facetTextOf,
    getVisibleDescriptors,
    markFilterableColumns,
    renderNumberText,
} from "../grid-config";
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

type VNode = { t: string; p: Record<string, unknown>; c?: unknown };
type Hyper = (tag: string, props: Record<string, unknown>, children?: unknown) => VNode;

const h: Hyper = (tag, props, children) => ({ t: tag, p: props, c: children });

type Leaf = { prop: string; name?: string; columnTemplate?: unknown; filter?: boolean };

function leavesOf(section: Parameters<typeof buildGridColumns>[0]): Leaf[] {
    const descriptors = getVisibleDescriptors(section, true, true);
    const columns = buildGridColumns(section, context);
    markFilterableColumns(columns, descriptors);
    return columns
        .flatMap((column) =>
            "children" in column && column.children?.length
                ? (column.children as unknown as Leaf[])
                : [column as unknown as Leaf],
        )
        .filter((leaf) => leaf.prop !== undefined);
}

function renderHeader(section: Parameters<typeof buildGridColumns>[0], prop: string) {
    const leaf = leavesOf(section).find((candidate) => String(candidate.prop) === prop);
    const template = leaf?.columnTemplate as
        | ((hyper: Hyper, data: { name?: string | undefined }) => VNode)
        | undefined;
    if (template === undefined) return undefined;
    const node = template(h, { name: leaf?.name });
    // The header renders the label AND the funnel button as its children.
    const children = node.c as unknown[];
    return {
        text: String(children?.[0]),
        stamped: node.p["data-prop"],
        hasFunnel: (children?.[1] as VNode | undefined)?.p?.class === "header-filter-button",
    };
}

test("each funnel's header stamps ITS OWN column prop", () => {
    // RevoGrid's header cell only carries `data-col` — a viewport-relative
    // index — so the funnel click used to resolve every column to the first
    // one and every popover listed the first column's values. The template
    // stamps the prop into the header, and that is what the click reads.
    const stamped = leavesOf("renovationAssets")
        .filter((leaf) => typeof leaf.columnTemplate === "function")
        .map((leaf) => renderHeader("renovationAssets", String(leaf.prop))?.stamped);
    expect(stamped).toEqual([
        "name",
        "repairCode",
        "repairCount",
        "totalCost",
        "createdAt",
        "updatedAt",
        "updatedBy",
    ]);
    expect(new Set(stamped).size).toBe(stamped.length);
});

test("the header still renders the column's own label", () => {
    expect(renderHeader("renovationAssets", "repairCode")?.text).toBe("Ремонтный код");
    expect(renderHeader("renovationAssets", "repairCount")?.text).toBe("Число ремонтных работ");
});

test("every column opts out of RevoGrid's own filter UI", () => {
    // RevoGrid's `.rv-filter` lives in a shadow root, so it cannot be restyled
    // and it opens RevoGrid's condition panel. The grid opts every column out
    // (`filter: false`) and renders the app's own funnel in the header template
    // instead — computed columns included, since they still carry a per-row
    // value.
    const leaves = leavesOf("renovationAssets");
    for (const leaf of leaves) {
        expect(leaf.filter).toBe(false);
    }
    for (const prop of ["name", "repairCode", "repairCount", "totalCost"]) {
        const header = renderHeader("renovationAssets", prop);
        expect(header?.hasFunnel).toBe(true);
    }
});

test("a numeric column is filtered by the text its cell shows", () => {
    // The facet list and the matching both read the formatted cell text, so
    // what the user ticks is literally what they see.
    const descriptor = {
        key: "repairCount",
        label: "Число ремонтных работ",
        width: 170,
        kind: "calculated" as const,
    };
    // Intl uses a non-breaking space as the thousands separator.
    expect(facetTextOf(descriptor, { repairCount: 1234.5 })).toBe(
        new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 6 }).format(1234.5),
    );
    expect(facetTextOf(descriptor, { repairCount: 0 })).toBe("0");
    // An empty aggregate is the «Пустые» bucket, not "0".
    expect(facetTextOf(descriptor, { repairCount: null })).toBeNull();
    expect(renderNumberText(3.25)).toBe("3,25");
});
