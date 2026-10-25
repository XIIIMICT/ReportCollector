import { expect, test } from "bun:test";

import { facetTextOf, renderDateTimeText } from "../grid-config";

test("a timestamp is rendered the same way in the cell and in the filter", () => {
    // The filter used to offer the raw ISO string while the cell showed a RU
    // date-time — the value the user picked did not exist anywhere on screen.
    const iso = "2026-03-04T09:15:30Z";
    const expected = new Intl.DateTimeFormat("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(new Date(iso));
    const descriptor = {
        key: "createdAt",
        label: "Создан",
        width: 180,
        kind: "datetime" as const,
    };
    expect(renderDateTimeText(iso)).toBe(expected);
    expect(facetTextOf(descriptor, { createdAt: iso })).toBe(expected);
    // The display carries no timezone marker, as the columns show none either.
    expect(expected).not.toMatch(/GMT|UTC|\+0\d:00|[Zz]$/);
});

test("an empty or unparsable timestamp is not a value", () => {
    const descriptor = {
        key: "createdAt",
        label: "Создан",
        width: 180,
        kind: "datetime" as const,
    };
    expect(facetTextOf(descriptor, { createdAt: null })).toBeNull();
    expect(facetTextOf(descriptor, { createdAt: "" })).toBeNull();
    // A value the browser cannot parse is shown as-is, like the cell does.
    expect(renderDateTimeText("not a date")).toBe("not a date");
});
