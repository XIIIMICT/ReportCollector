import { describe, expect, test } from "bun:test";

import type { HyperFunc, VNode } from "@revolist/react-datagrid";

import * as cellRendering from "../grid-cell-rendering";
import {
    booleanCellTemplate,
    cellProperties,
    dateTimeCellTemplate,
    fixedDecimalsCellTemplate,
    isCellReadOnly,
    multiValueCellTemplate,
    nextBooleanValue,
    numberCellTemplate,
    renderedDisplayValue,
    selectCellTemplate,
} from "../grid-cell-rendering";
import * as gridConfig from "../grid-config";
import type { ColumnDescriptor, GridColumnContext, GridRow, SectionId } from "../grid-config-types";

function descriptor(overrides: Partial<ColumnDescriptor> = {}): ColumnDescriptor {
    return { key: "flag", label: "Флаг", width: 100, kind: "text", ...overrides };
}

function context(overrides: Partial<GridColumnContext> = {}): GridColumnContext {
    return {
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
        ...overrides,
    };
}

function row(overrides: Partial<GridRow> = {}): GridRow {
    return {
        id: "row-1",
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        flag: true,
        ...overrides,
    };
}

type FakeNode = { tag: string; props: Record<string, unknown>; children?: unknown };
type FakeHyper = (tag: string, props: Record<string, unknown>, children?: unknown) => FakeNode;

const fakeH: FakeHyper = (tag, props, children) => ({ tag, props, children });
const h = fakeH as unknown as HyperFunc<VNode>;

function clickEvent() {
    let prevented = false;
    let stopped = false;
    const event = {
        preventDefault: () => {
            prevented = true;
        },
        stopPropagation: () => {
            stopped = true;
        },
    } as unknown as MouseEvent;
    return { event, wasPrevented: () => prevented, wasStopped: () => stopped };
}

const spaced = (text: string): string => text.replace(/\u00a0/g, " ");

describe("isCellReadOnly", () => {
    type Case = {
        name: string;
        section?: SectionId;
        column?: Partial<ColumnDescriptor>;
        ctx?: Partial<GridColumnContext>;
        data?: Partial<GridRow>;
        expected: boolean;
    };
    const cases: Case[] = [
        { name: "an ordinary cell stays editable", expected: false },
        {
            name: "a grid-wide read-only context locks it",
            ctx: { readOnly: true },
            expected: true,
        },
        {
            name: "a pinned copy is view-only",
            data: { __pinnedCopy: true },
            expected: true,
        },
        {
            name: "a remote lock under the canonical key locks it",
            data: { canonicalCells: { flag: "canon-1" } },
            ctx: { remoteLocks: new Map([["canon-1", { fullname: "Иван", email: "i@x" }]]) },
            expected: true,
        },
        {
            name: "a lock under the raw field key is ignored (canonical lookup only)",
            ctx: { remoteLocks: new Map([["flag", { fullname: "Иван", email: "i@x" }]]) },
            expected: false,
        },
        { name: "a fixed column is read-only", column: { fixed: true }, expected: true },
        { name: "a calculated cell is read-only", column: { kind: "calculated" }, expected: true },
        {
            name: "a non-admin cannot edit the projected price basis",
            section: "rawMaterialPrices",
            column: { key: "deliveryBasisName" },
            ctx: { isAdmin: false },
            expected: true,
        },
        {
            name: "an admin CAN edit the price basis",
            section: "rawMaterialPrices",
            column: { key: "deliveryBasisName" },
            expected: false,
        },
        { name: "a deleted row is read-only", data: { isDeleted: true }, expected: true },
        { name: "a view-only row is read-only", data: { readOnlyRow: true }, expected: true },
        {
            name: "a row-level lock on this cell is honoured",
            data: { lockedCells: ["flag"] },
            expected: true,
        },
        {
            name: "a row-level lock on another cell does not apply",
            data: { lockedCells: ["other"] },
            expected: false,
        },
        {
            name: "a descriptor-level readOnly column is locked",
            column: { readOnly: true },
            expected: true,
        },
        {
            name: "Keycloak locks email in the users dictionary",
            section: "users",
            column: { key: "email" },
            ctx: { keycloakEnabled: true },
            expected: true,
        },
        {
            name: "Keycloak locks fullname in the KPPK users dictionary",
            section: "kppkUsers",
            column: { key: "fullname" },
            ctx: { keycloakEnabled: true },
            expected: true,
        },
        {
            name: "without Keycloak email stays editable",
            section: "users",
            column: { key: "email" },
            expected: false,
        },
        {
            name: "the password reset flag is locked once set",
            column: { key: "resetPassword" },
            data: { resetPassword: true },
            expected: true,
        },
        {
            name: "an unset password reset flag stays editable",
            column: { key: "resetPassword" },
            expected: false,
        },
    ];
    for (const item of cases) {
        test(item.name, () => {
            expect(
                isCellReadOnly(
                    descriptor(item.column),
                    context(item.ctx),
                    row(item.data),
                    item.section ?? "materialMovement",
                ),
            ).toBe(item.expected);
        });
    }
});

describe("cellProperties", () => {
    const propsFor = (
        column: ColumnDescriptor,
        ctx: GridColumnContext,
        data: GridRow,
        index = 0,
        columnCount = 2,
    ) => cellProperties(column, ctx, data, "materialMovement", index, columnCount);

    test("the base class set, selection and last-column marker are preserved", () => {
        const result = propsFor(
            descriptor(),
            context({ selectedRowIds: new Set(["row-1"]) }),
            row(),
            1,
            2,
        );
        expect(result.className["report-cell"]).toBe(true);
        expect(result.className["report-cell--selected"]).toBe(true);
        expect(result.className["report-cell--last-column"]).toBe(true);
        expect(result.className["report-cell--readonly"]).toBe(false);
        expect(result.className["report-cell--remote-locked"]).toBe(false);
        expect(result["data-remote-lock"]).toBeUndefined();
    });

    test("remote presence is read by canonical key and carries the member's label", () => {
        const result = propsFor(
            descriptor(),
            context({
                remoteCellInfo: new Map([
                    ["canon-7", { name: "Иван", label: "A1:B2" }],
                    ["flag", { name: "Пётр", label: "C3" }],
                ]),
            }),
            row({ canonicalCells: { flag: "canon-7" } }),
        );
        expect(result.className["report-cell--remote-locked"]).toBe(true);
        expect(result["data-remote-lock"]).toBe("Иван — A1:B2");
    });

    test("a remote member without a selection label shows only the name", () => {
        const result = propsFor(
            descriptor(),
            context({ remoteCellInfo: new Map([["canon-7", { name: "Иван", label: null }]]) }),
            row({ canonicalCells: { flag: "canon-7" } }),
        );
        expect(result["data-remote-lock"]).toBe("Иван");
    });

    test("a remote map keyed by the raw field does not paint the cell", () => {
        const result = propsFor(
            descriptor(),
            context({ remoteCellInfo: new Map([["flag", { name: "Иван", label: "A1" }]]) }),
            row(),
        );
        expect(result.className["report-cell--remote-locked"]).toBe(false);
        expect(result["data-remote-lock"]).toBeUndefined();
    });

    test("the readonly class follows the shared readonly rule", () => {
        expect(
            propsFor(descriptor({ fixed: true }), context(), row()).className[
                "report-cell--readonly"
            ],
        ).toBe(true);
    });

    test("only a pinned copy carries its source id and marker on data cells", () => {
        const pinned = propsFor(
            descriptor(),
            context(),
            row({ __pinnedCopy: true, __pinSourceId: "src-9" }),
        );
        expect(pinned.className["data-grid__pinned-copy"]).toBe(true);
        expect(pinned["data-row-id"]).toBe("src-9");
        expect(pinned["data-pinned-copy"]).toBe("true");

        const ordinary = propsFor(descriptor(), context(), row());
        expect(ordinary.className["data-grid__pinned-copy"]).toBe(false);
        expect(ordinary["data-row-id"]).toBeUndefined();
        expect(ordinary["data-pinned-copy"]).toBeUndefined();
    });

    test("number and calculated columns carry the number class", () => {
        expect(
            propsFor(descriptor({ kind: "number" }), context(), row()).className[
                "report-cell--number"
            ],
        ).toBe(true);
        expect(
            propsFor(descriptor({ kind: "calculated" }), context(), row()).className[
                "report-cell--number"
            ],
        ).toBe(true);
        expect(propsFor(descriptor(), context(), row()).className["report-cell--number"]).toBe(
            false,
        );
    });

    test("computed cells are green on real rows, neutral in a plain add-row", () => {
        expect(
            propsFor(descriptor({ kind: "calculated" }), context(), row()).className[
                "report-cell--calculated"
            ],
        ).toBe(true);
        const placeholder = propsFor(
            descriptor({ kind: "calculated" }),
            context(),
            row({ isPlaceholder: true }),
        );
        expect(placeholder.className["report-cell--placeholder"]).toBe(true);
        expect(placeholder.className["report-cell--calculated"]).toBe(false);
    });

    test("a preview draft is green in the add-row without the empty tint", () => {
        const preview = propsFor(
            descriptor({ kind: "calculated" }),
            context(),
            row({ isPlaceholder: true, highlightCalculatedInPlaceholder: true }),
        );
        expect(preview.className["report-cell--calculated"]).toBe(true);
        expect(preview.className["report-cell--empty"]).toBe(false);
    });

    test("a deleted row never carries the calculated highlight", () => {
        const result = propsFor(
            descriptor({ kind: "calculated" }),
            context(),
            row({ isDeleted: true }),
        );
        expect(result.className["report-cell--calculated"]).toBe(false);
    });

    test("the empty tint covers null/undefined/empty string/empty array only on real rows", () => {
        for (const value of [null, undefined, "", []]) {
            expect(
                propsFor(descriptor(), context(), row({ flag: value })).className[
                    "report-cell--empty"
                ],
            ).toBe(true);
        }
        expect(
            propsFor(descriptor(), context(), row({ flag: 0 })).className["report-cell--empty"],
        ).toBe(false);
        expect(
            propsFor(descriptor(), context(), row({ flag: [], isPlaceholder: true })).className[
                "report-cell--empty"
            ],
        ).toBe(false);
        expect(
            propsFor(descriptor(), context(), row({ flag: null, isDeleted: true })).className[
                "report-cell--empty"
            ],
        ).toBe(false);
    });

    test("diff classes appear only with a baseline and outside calculated cells", () => {
        const withBaseline = context({ hasBaseline: true });
        const changed = propsFor(descriptor(), withBaseline, row({ changedFields: ["flag"] }));
        expect(changed.className["report-cell--changed"]).toBe(true);
        expect(changed.className["report-cell--empty"]).toBe(false);
        const created = propsFor(descriptor(), withBaseline, row({ rowState: "new" }));
        expect(created.className["report-cell--new"]).toBe(true);
        const removed = propsFor(descriptor(), withBaseline, row({ isDeleted: true }));
        expect(removed.className["report-cell--deleted"]).toBe(true);
        const calculated = propsFor(
            descriptor({ kind: "calculated" }),
            withBaseline,
            row({ changedFields: ["flag"] }),
        );
        expect(calculated.className["report-cell--changed"]).toBe(false);
        expect(
            propsFor(descriptor(), context(), row({ changedFields: ["flag"] })).className[
                "report-cell--changed"
            ],
        ).toBe(false);
    });
});

describe("nextBooleanValue", () => {
    test("a plain column toggles false ⇄ true and never clears", () => {
        expect(nextBooleanValue(null, false)).toBe(true);
        expect(nextBooleanValue(undefined, false)).toBe(true);
        expect(nextBooleanValue(false, false)).toBe(true);
        expect(nextBooleanValue(true, false)).toBe(false);
    });

    test("a nullable column cycles пусто → Да → Нет → пусто", () => {
        const cycle = [null, undefined, true, false].map((value) => nextBooleanValue(value, true));
        expect(cycle).toEqual([true, true, false, null]);
        expect(nextBooleanValue(nextBooleanValue(false, true), true)).toBe(true);
    });
});

describe("display value and text cell templates", () => {
    test("renderedDisplayValue reads only the requested row field", () => {
        expect(renderedDisplayValue({ orgShort: "Короткое", flag: true }, "orgShort")).toBe(
            "Короткое",
        );
        expect(renderedDisplayValue({ orgShort: "Короткое" }, "missing")).toBeUndefined();
        expect(renderedDisplayValue(undefined, "orgShort")).toBeUndefined();
    });

    test("a display value is rendered through the multi-value template", () => {
        const value = renderedDisplayValue({ orgShort: "ДЗО-1, ДЗО-2" }, "orgShort");
        const node = multiValueCellTemplate(h, { value }) as unknown as FakeNode;
        expect(node.props["class"]).toBe("report-cell__multi-value");
        expect(node.props["title"]).toBe("ДЗО-1, ДЗО-2");
        expect(node.children).toBe("ДЗО-1, ДЗО-2");
    });

    test("multi-value fields drop blanks and non-strings", () => {
        const node = multiValueCellTemplate(h, {
            value: ["Иван", "", "  ", 42, null, "Пётр "],
        }) as unknown as FakeNode;
        // Array labels keep their original text — only the blank check trims.
        expect(node.props["title"]).toBe("Иван, Пётр ");
        expect(node.children).toBe("Иван, Пётр ");
        expect((multiValueCellTemplate(h, {}) as unknown as FakeNode).children).toBe("");
    });

    test("select cells show the empty label, the option label or the raw value", () => {
        const options = [
            { value: "blocked", label: "Заблокирован" },
            { value: "active", label: "Активен" },
        ];
        expect(
            (selectCellTemplate(h, { value: null }, options, "Нет") as unknown as FakeNode)
                .children,
        ).toBe("Нет");
        expect(
            (selectCellTemplate(h, { value: "" }, options, "Нет") as unknown as FakeNode).children,
        ).toBe("Нет");
        expect(
            (selectCellTemplate(h, { value: "blocked" }, options) as unknown as FakeNode).children,
        ).toBe("Заблокирован");
        expect(
            (selectCellTemplate(h, { value: "unknown" }, options) as unknown as FakeNode).children,
        ).toBe("unknown");
    });

    test("number, fixed-decimals and datetime text cells keep their formatting", () => {
        expect(
            spaced(
                (numberCellTemplate(h, { value: 1234.5 }) as unknown as FakeNode)
                    .children as string,
            ),
        ).toBe("1 234,5");
        expect((numberCellTemplate(h, { value: null }) as unknown as FakeNode).children).toBe("");
        expect(
            spaced(
                (fixedDecimalsCellTemplate(2)(h, { value: "1234,5" }) as unknown as FakeNode)
                    .children as string,
            ),
        ).toBe("1 234,50");
        expect(
            (fixedDecimalsCellTemplate(2)(h, { value: "" }) as unknown as FakeNode).children,
        ).toBe("");
        const instant = new Date("2026-11-26T10:20:30");
        expect(
            (dateTimeCellTemplate(h, { value: instant.toISOString() }) as unknown as FakeNode)
                .children,
        ).toBe("26.11.2026, 10:20:30");
        expect((dateTimeCellTemplate(h, { value: "" }) as unknown as FakeNode).children).toBe("");
    });
});

describe("booleanCellTemplate", () => {
    test("a plain column renders an unchecked checkbox when the value is missing", () => {
        const node = booleanCellTemplate(
            h,
            { model: row({ flag: null }) } as never,
            descriptor({ kind: "boolean" }),
            context(),
            "materialMovement",
        ) as unknown as FakeNode;
        expect(node.props["class"]).toBe("report-cell__checkbox");
        const input = node.children as FakeNode;
        expect(input.tag).toBe("input");
        expect(input.props["type"]).toBe("checkbox");
        expect(input.props["checked"]).toBe(false);
        expect(input.props["disabled"]).toBe(false);
        expect(input.props["ariaLabel"]).toBe("Флаг");
    });

    test("a click cancels the native toggle and commits the next value", () => {
        const changes: Array<unknown[]> = [];
        const node = booleanCellTemplate(
            h,
            { model: row({ flag: true }), value: true } as never,
            descriptor({ kind: "boolean", allowsNull: true }),
            context({
                onBooleanChange: (...args) => {
                    changes.push(args);
                },
            }),
            "materialMovement",
        ) as unknown as FakeNode;
        const input = node.children as FakeNode;
        expect(input.props["checked"]).toBe(true);
        const event = clickEvent();
        (input.props["onClick"] as (event: MouseEvent) => void)(event.event);
        expect(event.wasStopped()).toBe(true);
        expect(event.wasPrevented()).toBe(true);
        // «Да» → «Нет» for a nullable column.
        expect(changes).toEqual([[row(), "flag", false]]);
    });

    test("a nullable empty cell has no checkbox and its first click sets «Да»", () => {
        const changes: Array<unknown[]> = [];
        const model = row({ flag: null });
        const node = booleanCellTemplate(
            h,
            { model, value: null } as never,
            descriptor({ kind: "boolean", allowsNull: true }),
            context({
                onBooleanChange: (...args) => {
                    changes.push(args);
                },
            }),
            "materialMovement",
        ) as unknown as FakeNode;
        expect(node.props["class"]).toBe("report-cell__checkbox report-cell__checkbox--empty");
        expect(node.props["aria-label"]).toBe("Флаг");
        expect(node.children).toBeUndefined();
        (node.props["onClick"] as (event: MouseEvent) => void)(clickEvent().event);
        expect(changes).toEqual([[model, "flag", true]]);
    });

    test("a read-only cell renders a disabled checkbox and ignores clicks", () => {
        let called = false;
        const node = booleanCellTemplate(
            h,
            { model: row(), value: true } as never,
            descriptor({ kind: "boolean", fixed: true }),
            context({
                onBooleanChange: () => {
                    called = true;
                },
            }),
            "materialMovement",
        ) as unknown as FakeNode;
        const input = node.children as FakeNode;
        expect(input.props["disabled"]).toBe(true);
        (input.props["onClick"] as (event: MouseEvent) => void)(clickEvent().event);
        expect(called).toBe(false);
    });
});

test("grid-config re-exports every moved cell-rendering name by identity", () => {
    const config = gridConfig as unknown as Record<string, unknown>;
    const moved = cellRendering as unknown as Record<string, unknown>;
    const names = [
        "isCellReadOnly",
        "cellProperties",
        "nextBooleanValue",
        "booleanCellTemplate",
        "multiValueCellTemplate",
        "renderedDisplayValue",
        "selectCellTemplate",
        "numberCellTemplate",
        "fixedDecimalsCellTemplate",
        "dateTimeCellTemplate",
    ] as const;
    for (const name of names) {
        expect(config[name]).toBe(moved[name]);
        expect(typeof config[name]).toBe("function");
    }
    for (const builder of ["exportColumnsFromGrid", "buildGridColumns", "markFilterableColumns"]) {
        expect(typeof config[builder]).toBe("function");
    }
});
