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
    return { text: String(node.c), stamped: node.p["data-prop"] };
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

test("computed columns are filterable — they carry a per-row number", () => {
    // «Покажи предприятия без ремонтных работ» is a normal question, so a
    // computed column must keep its funnel instead of being opted out.
    for (const prop of ["repairCount", "totalCost"]) {
        const leaf = leavesOf("renovationAssets").find(
            (candidate) => String(candidate.prop) === prop,
        );
        expect(typeof leaf?.columnTemplate).toBe("function");
        expect(leaf?.filter).not.toBe(false);
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
