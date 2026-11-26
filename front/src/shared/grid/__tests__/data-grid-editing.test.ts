import { describe, expect, test } from "bun:test";

import {
    checkEdit,
    checkEditStart,
    normalizeRangePatch,
    uneditableReason,
    type EditArgs,
    type NormalizeOutcome,
    type RangePatchArgs,
    type RemoteOwner,
} from "@/shared/grid/data-grid-editing";
import type { ColumnDescriptor, GridRow } from "@/shared/grid/grid-config-types";
import { groupSkippedCells } from "@/shared/grid/skipped-cell-reasons";

function owner(fullname: string, email: string): RemoteOwner {
    return { fullname, email };
}

function makeRow(id: string, canonicalCells: Record<string, string> = {}): GridRow {
    return { id, rowState: "unchanged", changedFields: [], isDeleted: false, canonicalCells };
}

function makeDescriptor(overrides: Partial<ColumnDescriptor> = {}): ColumnDescriptor {
    return { key: "field", label: "Поле", width: 120, kind: "text", ...overrides };
}

function checkArgs(overrides: Partial<EditArgs> = {}): EditArgs {
    return {
        descriptor: makeDescriptor(),
        row: makeRow("r1", { field: "r1.field" }),
        editableKeys: new Set(["field"]),
        canEdit: () => true,
        remoteLocks: new Map<string, RemoteOwner>(),
        remotePresence: new Map<string, RemoteOwner>(),
        activeLease: null,
        ...overrides,
    };
}

function asPatches(value: Record<string, Record<string, unknown>>): Record<string, GridRow> {
    return value as unknown as Record<string, GridRow>;
}

const trimValue = (value: unknown): NormalizeOutcome => ({
    ok: true,
    value: typeof value === "string" ? value.trim() : value,
});

function rangeArgs(overrides: Partial<RangePatchArgs> = {}): RangePatchArgs {
    return {
        patches: asPatches({ "0": { field: "  значение  " } }),
        models: {},
        rows: [makeRow("r0", { field: "r0.field" })],
        descriptors: new Map([["field", makeDescriptor()]]),
        editableKeys: new Set(["field"]),
        canEdit: () => true,
        remoteLocks: new Map<string, RemoteOwner>(),
        remotePresence: new Map<string, RemoteOwner>(),
        normalizeValue: trimValue,
        ...overrides,
    };
}

describe("checkEditStart", () => {
    test("allows an editable unlocked cell and reports its canonical key", () => {
        const descriptor = makeDescriptor();
        const decision = checkEditStart(checkArgs({ descriptor }));
        expect(decision).toEqual({
            allowed: true,
            descriptor,
            canonicalKey: "r1.field",
        });
    });

    test("denies a missing descriptor without an owner", () => {
        expect(checkEditStart(checkArgs({ descriptor: undefined }))).toEqual({
            allowed: false,
            canonicalKey: undefined,
        });
    });

    test("denies a field outside editableKeys", () => {
        expect(checkEditStart(checkArgs({ editableKeys: new Set(["other"]) }))).toEqual({
            allowed: false,
            canonicalKey: undefined,
        });
    });

    test("denies when canEdit rejects the row", () => {
        const row = makeRow("r1", { field: "r1.field" });
        expect(
            checkEditStart(
                checkArgs({
                    row,
                    canEdit: (_descriptor, candidate) => candidate.id !== row.id,
                }),
            ),
        ).toEqual({ allowed: false, canonicalKey: undefined });
    });

    test("a remote lock blocks and prefers the full name", () => {
        const decision = checkEditStart(
            checkArgs({
                remoteLocks: new Map([["r1.field", owner("Иванов Иван", "ivan@test.local")]]),
            }),
        );
        expect(decision).toEqual({
            allowed: false,
            canonicalKey: "r1.field",
            owner: "Иванов Иван",
        });
    });

    test("a foreign selection blocks and falls back to the email", () => {
        const decision = checkEditStart(
            checkArgs({
                remotePresence: new Map([["r1.field", owner("", "petr@test.local")]]),
            }),
        );
        expect(decision.allowed).toBe(false);
        expect(decision.allowed === false ? decision.owner : undefined).toBe("petr@test.local");
    });

    test("both owner fields empty use the exact Russian fallback", () => {
        const decision = checkEditStart(
            checkArgs({ remotePresence: new Map([["r1.field", owner("", "")]]) }),
        );
        expect(decision.allowed === false ? decision.owner : undefined).toBe("другой пользователь");
    });

    test("a lock wins over presence when both maps hold the key", () => {
        const decision = checkEditStart(
            checkArgs({
                remoteLocks: new Map([["r1.field", owner("Земелькин Сергей", "z@test.local")]]),
                remotePresence: new Map([["r1.field", owner("Другой", "d@test.local")]]),
            }),
        );
        expect(decision.allowed === false ? decision.owner : undefined).toBe("Земелькин Сергей");
    });

    test("a row without a canonical key is not blocked by an empty-string entry", () => {
        const decision = checkEditStart(
            checkArgs({
                row: makeRow("r1"),
                remoteLocks: new Map([["", owner("", "")]]),
                remotePresence: new Map([["", owner("", "")]]),
            }),
        );
        expect(decision.allowed).toBe(true);
        expect(decision.allowed === true ? decision.canonicalKey : undefined).toBeUndefined();
    });
});

describe("checkEdit", () => {
    test("my active lease beats a foreign selection", () => {
        const decision = checkEdit(
            checkArgs({
                activeLease: "r1.field",
                remotePresence: new Map([["r1.field", owner("Другой", "d@test.local")]]),
            }),
        );
        expect(decision.allowed).toBe(true);
    });

    test("a foreign selection blocks when I hold another lease", () => {
        const decision = checkEdit(
            checkArgs({
                activeLease: "r1.other",
                remotePresence: new Map([["r1.field", owner("Другой", "d@test.local")]]),
            }),
        );
        expect(decision).toEqual({
            allowed: false,
            canonicalKey: "r1.field",
            owner: "Другой",
        });
    });

    test("a foreign selection blocks when no lease is active", () => {
        const decision = checkEdit(
            checkArgs({
                activeLease: null,
                remotePresence: new Map([["r1.field", owner("", "")]]),
            }),
        );
        expect(decision.allowed === false ? decision.owner : undefined).toBe("другой пользователь");
    });

    test("a foreign lock blocks even when it is my active lease", () => {
        const decision = checkEdit(
            checkArgs({
                activeLease: "r1.field",
                remoteLocks: new Map([["r1.field", owner("", "lock@test.local")]]),
            }),
        );
        expect(decision).toEqual({
            allowed: false,
            canonicalKey: "r1.field",
            owner: "lock@test.local",
        });
    });

    test("missing descriptor, non-editable field and canEdit denial match checkEditStart", () => {
        const cases = [
            checkArgs({ descriptor: undefined }),
            checkArgs({ editableKeys: new Set() }),
            checkArgs({ canEdit: () => false }),
        ];
        for (const args of cases) {
            expect(checkEdit({ ...args, activeLease: null })).toEqual({
                allowed: false,
                canonicalKey: undefined,
            });
        }
    });
});

describe("uneditableReason", () => {
    test("calculated wins over fixed", () => {
        expect(uneditableReason(makeDescriptor({ kind: "calculated", fixed: true }))).toBe(
            "расчётное поле",
        );
    });

    test("fixed yields its reason", () => {
        expect(uneditableReason(makeDescriptor({ fixed: true }))).toBe("фиксированное поле");
    });

    test("anything else yields the generic Russian reason", () => {
        expect(uneditableReason(makeDescriptor({ kind: "number" }))).toBe(
            "поле недоступно для редактирования",
        );
    });
});

describe("normalizeRangePatch", () => {
    test("mutates the patch in place and normalizes values for their descriptor kind", () => {
        const patches = asPatches({ "0": { field: "  значение  ", other: "  другое  " } });
        const kinds: ColumnDescriptor["kind"][] = [];
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                descriptors: new Map([
                    ["field", makeDescriptor()],
                    ["other", makeDescriptor({ key: "other", label: "Другое", kind: "number" })],
                ]),
                editableKeys: new Set(["field", "other"]),
                normalizeValue: (value, kind) => {
                    kinds.push(kind);
                    return trimValue(value);
                },
            }),
        );
        expect(result).toEqual({ skipped: new Map(), invalid: null });
        expect(patches["0"]! as Record<string, unknown>).toEqual({
            field: "значение",
            other: "другое",
        });
        expect(kinds).toEqual(["text", "number"]);
    });

    test("a field without a descriptor is deleted silently", () => {
        const patches = asPatches({ "0": { ghost: "x", field: " ok " } });
        const result = normalizeRangePatch(rangeArgs({ patches }));
        expect(Object.keys(patches["0"]!)).toEqual(["field"]);
        expect(result.skipped.size).toBe(0);
        expect(result.invalid).toBeNull();
    });

    test("non-editable and canEdit-denied fields are deleted with their reason", () => {
        const patches = asPatches({
            "0": { calc: "1", lockedField: "2", plain: "3", field: "4" },
        });
        const descriptors = new Map([
            ["calc", makeDescriptor({ key: "calc", label: "Расчёт", kind: "calculated" })],
            ["lockedField", makeDescriptor({ key: "lockedField", label: "Фикс", fixed: true })],
            ["plain", makeDescriptor({ key: "plain", label: "Простое" })],
            ["field", makeDescriptor()],
        ]);
        const row: GridRow = {
            ...makeRow("r0", {
                calc: "r0.calc",
                lockedField: "r0.lockedField",
                plain: "r0.plain",
                field: "r0.field",
            }),
            lockedCells: ["plain"],
        };
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                rows: [row],
                descriptors,
                editableKeys: new Set(["plain", "field"]),
                canEdit: (descriptor, candidate) =>
                    !candidate.lockedCells?.includes(descriptor.key),
            }),
        );
        expect(Object.keys(patches["0"]!)).toEqual(["field"]);
        expect([...result.skipped]).toEqual([
            ["Расчёт", "расчётное поле"],
            ["Фикс", "фиксированное поле"],
            ["Простое", "поле недоступно для редактирования"],
        ]);
    });

    test("a missing row deletes the field and records the reason", () => {
        const patches = asPatches({ "7": { field: "x" } });
        const result = normalizeRangePatch(rangeArgs({ patches, rows: [] }));
        expect(Object.keys(patches["7"]!)).toEqual([]);
        expect([...result.skipped]).toEqual([["Поле", "поле недоступно для редактирования"]]);
    });

    test("busy canonical cells are deleted by lock or presence, empty keys are ignored", () => {
        const patches = asPatches({
            "0": { lockField: "a", presenceField: "b", emptyKeyField: "c", field: "d" },
        });
        const descriptors = new Map([
            ["lockField", makeDescriptor({ key: "lockField", label: " leased " })],
            ["presenceField", makeDescriptor({ key: "presenceField", label: " selected " })],
            ["emptyKeyField", makeDescriptor({ key: "emptyKeyField", label: " Пустое " })],
            ["field", makeDescriptor()],
        ]);
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                rows: [
                    makeRow("r0", {
                        lockField: "r0.lease",
                        presenceField: "r0.presence",
                        emptyKeyField: "",
                        field: "r0.field",
                    }),
                ],
                descriptors,
                editableKeys: new Set(["lockField", "presenceField", "emptyKeyField", "field"]),
                remoteLocks: new Map([["r0.lease", owner("", "")]]),
                remotePresence: new Map([["r0.presence", owner("", "")]]),
            }),
        );
        expect(Object.keys(patches["0"]!)).toEqual(["emptyKeyField", "field"]);
        expect([...result.skipped]).toEqual([
            [" leased ", "ячейка занята другим пользователем"],
            [" selected ", "ячейка занята другим пользователем"],
        ]);
    });

    test("normalization failure stops the scan at the first invalid value", () => {
        const patches = asPatches({
            "0": { calc: "1", bad: "BAD", after: "  later  " },
            "1": { field: "  next row  " },
        });
        const seen: unknown[] = [];
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                rows: [makeRow("r0", { field: "r0.field" }), makeRow("r1", { field: "r1.field" })],
                descriptors: new Map([
                    ["calc", makeDescriptor({ key: "calc", label: "Расчёт", kind: "calculated" })],
                    ["bad", makeDescriptor({ key: "bad", label: "Плохое" })],
                    ["after", makeDescriptor({ key: "after", label: "После" })],
                    ["field", makeDescriptor()],
                ]),
                editableKeys: new Set(["bad", "after", "field"]),
                normalizeValue: (value, _kind) => {
                    seen.push(value);
                    return value === "BAD"
                        ? { ok: false, message: "введите конечное неотрицательное число." }
                        : trimValue(value);
                },
            }),
        );
        expect(result.invalid).toBe("введите конечное неотрицательное число.");
        expect(seen).toEqual(["BAD"]);
        expect(Object.keys(patches["0"]!)).toEqual(["bad", "after"]);
        expect(patches["0"]!.after).toBe("  later  ");
        expect(Object.keys(patches["1"]!)).toEqual(["field"]);
        expect(patches["1"]!.field).toBe("  next row  ");
        expect([...result.skipped]).toEqual([["Расчёт", "расчётное поле"]]);
    });

    test("models take precedence over rows and are resolved by numeric row index", () => {
        const patches = asPatches({
            "0": { field: "a" },
            "1": { field: "b" },
            "2": { field: "c" },
        });
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                models: {
                    "0": { ...makeRow("model0"), lockedCells: ["field"] },
                    "1": undefined,
                    "2": makeRow("model2", { field: "model2.field" }),
                },
                rows: [
                    makeRow("row0"),
                    makeRow("row1", { field: "row1.field" }),
                    makeRow("row2", { field: "row2.field" }),
                ],
                remotePresence: new Map([["row1.field", owner("", "")]]),
                canEdit: (descriptor, candidate) =>
                    !candidate.lockedCells?.includes(descriptor.key),
            }),
        );
        expect(Object.keys(patches["0"]!)).toEqual([]);
        expect(Object.keys(patches["1"]!)).toEqual([]);
        expect(patches["2"]! as Record<string, unknown>).toEqual({ field: "c" });
        // Skipped entries are keyed by label, so the second row overwrites the
        // first reason for the same descriptor — exactly as the inline Map did.
        expect([...result.skipped]).toEqual([["Поле", "ячейка занята другим пользователем"]]);
    });

    test("skipped labels can be fed straight to groupSkippedCells", () => {
        const patches = asPatches({
            "0": {
                fixed: "1",
                calc: "2",
                busy: "3",
                plain: "4",
                field: "5",
            },
        });
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                rows: [makeRow("r0", { busy: "r0.busy" })],
                descriptors: new Map([
                    [
                        "fixed",
                        makeDescriptor({ key: "fixed", label: "Сырьевая позиция", fixed: true }),
                    ],
                    [
                        "calc",
                        makeDescriptor({
                            key: "calc",
                            label: "Потребность / План, т",
                            kind: "calculated",
                        }),
                    ],
                    ["busy", makeDescriptor({ key: "busy", label: "Объём, т" })],
                    ["plain", makeDescriptor({ key: "plain", label: "Базис поставки" })],
                    ["field", makeDescriptor()],
                ]),
                editableKeys: new Set(["busy", "field"]),
                remoteLocks: new Map([["r0.busy", owner("", "")]]),
            }),
        );
        expect(groupSkippedCells(result.skipped)).toBe(
            "«Сырьевая позиция»: фиксированное поле; «Потребность / План, т»: расчётное поле; «Объём, т»: ячейка занята другим пользователем; «Базис поставки»: поле недоступно для редактирования",
        );
    });

    test("a repeated label keeps the last reason, matching the inline Map", () => {
        const patches = asPatches({ "0": { first: "1", second: "2" } });
        const result = normalizeRangePatch(
            rangeArgs({
                patches,
                rows: [makeRow("r0")],
                descriptors: new Map([
                    ["first", makeDescriptor({ key: "first", label: "Один", fixed: true })],
                    ["second", makeDescriptor({ key: "second", label: "Один" })],
                ]),
                editableKeys: new Set(["first", "second"]),
                canEdit: (descriptor) => descriptor.key !== "second",
            }),
        );
        expect([...result.skipped]).toEqual([["Один", "поле недоступно для редактирования"]]);
    });
});
