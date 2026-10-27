import { afterEach, expect, test } from "bun:test";

import { readToolbarFilters, restoredFilterVerdict } from "@/shared/ui/use-toolbar-filters";

afterEach(() => {
    window.location.href = "http://localhost:5000/renovation";
});

/**
 * The rule that keeps a restored panel filter alive while its options load.
 *
 * The bug it fixes: «Блок дирекции = МТО» disappeared on every reload because
 * the guard compared the stored value against the block list on the FIRST
 * render, when that list is still empty — so a valid block looked stale and was
 * deleted from the URL. Same shape for the raw materials archive version.
 */
test("a value is PENDING, not cleared, while its options are unknown", () => {
    // The options have not arrived: we cannot know whether «mto» is valid.
    expect(restoredFilterVerdict("mto", null)).toBe("pending");
});

test("a value present in the loaded options is kept", () => {
    expect(restoredFilterVerdict("mto", ["ksir", "mto", "tech"])).toBe("keep");
});

test("a value absent from the loaded options is cleared", () => {
    // A dropped directorate would filter every row away.
    expect(restoredFilterVerdict("dropped", ["ksir", "mto", "tech"])).toBe("clear");
});

test("nothing stored is always fine", () => {
    expect(restoredFilterVerdict(null, null)).toBe("keep");
    expect(restoredFilterVerdict(null, [])).toBe("keep");
});

test("an EMPTY known list clears — it is a real answer, not an absence", () => {
    // `null` means "not loaded"; `[]` means "loaded, and nothing exists".
    expect(restoredFilterVerdict("mto", [])).toBe("clear");
});

test("the surrounding URL is untouched by the verdict", () => {
    window.location.href = "http://localhost:5000/renovation?f.plan.block=mto&section=plan";
    // Reading the filter is a pure read: the value stays in the address.
    expect(readToolbarFilters("plan")["block"]).toBe("mto");
    expect(window.location.search).toContain("f.plan.block=mto");
    // Pending must not rewrite anything either.
    expect(restoredFilterVerdict("mto", null)).toBe("pending");
    expect(window.location.search).toContain("f.plan.block=mto");
});
