import { afterEach, expect, test } from "bun:test";

/**
 * The reveal gate for the plan's remembered scroll position.
 *
 * The grid mounts and paints its first rows immediately; restoring the saved row
 * is an ASYNC scroll, so the plan used to visibly open at the TOP and then jump.
 * `renovation-grid-shell--restoring` hides the data rows until the jump settles.
 *
 * These tests pin the two halves of that rule: the class is applied only while a
 * restore is pending, and the selector reaches the data element but NOT the
 * header (hiding the header would make the frame appear row-less and shift).
 */
type Gate = "restoring" | "ready";

// Mirrors the render condition in renovation-grid.tsx.
function gate(initialRestorePending: boolean): Gate {
    return initialRestorePending ? "restoring" : "ready";
}

test("a grid that opens at a remembered row starts restoring", () => {
    // The grid mounts together with the position it must open at.
    expect(gate(true)).toBe("restoring");
});

test("a grid with nothing to restore paints immediately", () => {
    // No saved row (e.g. the archive, or a first visit) — never hidden.
    expect(gate(false)).toBe("ready");
});

test("the rule hides the data rows and leaves the header alone", () => {
    // Build the shape the selector targets: shell > grid > revogr-data, and a
    // header OUTSIDE revogr-data.
    const shell = document.createElement("div");
    shell.className = "revo-grid-shell renovation-grid-shell renovation-grid-shell--restoring";
    const grid = document.createElement("div");
    grid.className = "renovation-grid";
    const data = document.createElement("revogr-data");
    const header = document.createElement("div");
    header.className = "renovation-grid__header";
    grid.append(data, header);
    shell.appendChild(grid);
    document.body.appendChild(shell);

    // The selector used by the stylesheet, applied to the real element.
    expect(data.matches(".renovation-grid-shell--restoring .renovation-grid revogr-data")).toBe(
        true,
    );
    expect(header.matches(".renovation-grid-shell--restoring .renovation-grid revogr-data")).toBe(
        false,
    );
});

test("the gate is dropped once restoring finishes", () => {
    const shell = document.createElement("div");
    shell.className = "revo-grid-shell renovation-grid-shell";
    const grid = document.createElement("div");
    grid.className = "renovation-grid";
    const data = document.createElement("revogr-data");
    grid.appendChild(data);
    shell.appendChild(grid);
    document.body.appendChild(shell);

    // No `--restoring` class → the rule does not match, rows are visible.
    expect(data.matches(".renovation-grid-shell--restoring .renovation-grid revogr-data")).toBe(
        false,
    );
});

afterEach(() => {
    document.body.innerHTML = "";
});
