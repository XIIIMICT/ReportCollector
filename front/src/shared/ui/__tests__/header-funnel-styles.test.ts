import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const css = readFileSync(
    new URL("../../../app/globals.css", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
    "utf8",
);

/**
 * The funnel's rules live in one stylesheet, so a regression is invisible to
 * the component tests. Each of these shipped broken at least once.
 */
test("the funnel has a base state AND a reveal", () => {
    expect(css).toMatch(/\.header-filter-button\s*\{[^}]*width:\s*0;/);
    expect(css).toMatch(/\.header-filter-button\s*\{[^}]*opacity:\s*0;/);
    const reveal = css.slice(css.indexOf("The reveal:"));
    expect(reveal).toContain(".renovation-grid__header-cell:hover .header-filter-button");
    expect(reveal).toContain(".renovation-grid__corner:hover .header-filter-button");
    expect(reveal).toContain(".rv-filter");
    expect(reveal).toMatch(/width:\s*24px;/);
    expect(reveal).toMatch(/opacity:\s*1;/);
});

test("an active funnel is tinted with the accent", () => {
    expect(css).toMatch(/\.header-filter-button\.is-active\s*\{[^}]*color:\s*var\(--accent\)/);
});

test("RevoGrid's funnel is pinned to the cell's right edge", () => {
    // The plan's button is absolutely positioned against the right edge, so
    // widening SLIDES it out of the edge. RevoGrid's sits in the header-controls
    // flow, where it would grow in place — the two must be pinned the same way
    // or the animation reads differently.
    const block = css.slice(css.indexOf(".rgHeaderCell .header-controls {"));
    expect(block.slice(0, block.indexOf("}"))).toContain("position: absolute");
    expect(block.slice(0, block.indexOf("}"))).toContain("right: 1px");
});

test("RevoGrid's label makes room for the funnel on the button's own states", () => {
    // Keyed on `.is-active` (which only the plan's buttons carry) the label
    // never moved; keyed on `margin` it shrank around a centred text instead of
    // shifting it. It must be `padding`, on the states the funnel itself uses.
    const block = css.slice(css.indexOf(".rgHeaderCell:hover .header-content"));
    const rule = block.slice(0, block.indexOf("}"));
    expect(rule).toContain(".rgHeaderCell:has(.rv-filter.active) .header-content");
    expect(rule).toContain("padding-right: 24px");
});

test("no stale per-project funnel class is left in the stylesheet", () => {
    expect(css).not.toContain(".renovation-grid__filter-button");
    expect(css).not.toContain(".renovation-grid__filter-icon");
});
