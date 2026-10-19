import { describe, expect, test } from "bun:test";

import { nextBooleanValue } from "../grid-config";

describe("boolean cell toggling", () => {
    test("a plain column is a binary toggle (dictionaries: «Доступ к проекту»)", () => {
        expect(nextBooleanValue(null, false)).toBe(true);
        expect(nextBooleanValue(undefined, false)).toBe(true);
        expect(nextBooleanValue(false, false)).toBe(true);
        expect(nextBooleanValue(true, false)).toBe(false);
        // Never clears: the column has no «пусто» state.
        expect(nextBooleanValue(false, false)).not.toBeNull();
    });

    test("a nullable column cycles пусто → Да → Нет → пусто (the plan's «Наличие МТР»)", () => {
        // An EMPTY cell (no checkbox at all) sets «Да» on the first click.
        expect(nextBooleanValue(null, true)).toBe(true);
        expect(nextBooleanValue(undefined, true)).toBe(true);
        // Checked → «Нет».
        expect(nextBooleanValue(true, true)).toBe(false);
        // «Нет» (an UNCHECKED box) clears the value — the same as Delete;
        // the cell then renders with no checkbox at all.
        expect(nextBooleanValue(false, true)).toBeNull();
        // And around again.
        expect(nextBooleanValue(nextBooleanValue(false, true), true)).toBe(true);
    });
});
