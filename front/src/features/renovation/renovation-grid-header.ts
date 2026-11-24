/**
 * Header geometry of the renovation plan grid: widths, level heights, resize
 * handle clipping and the column number strip.
 *
 * Split out of `renovation-grid.tsx`. The plan draws its own 11-level header
 * over the grid (revo-grid's native one cannot render it), so these are the
 * pure geometry helpers that header needs — no React, no grid instance.
 */

import {
    HEADER_LEVEL_COUNT,
    HEADER_LEVEL_HEIGHT,
    HEADER_LEVEL_HEIGHT_STORAGE_KEY,
    MAX_COLUMN_WIDTH,
    MAX_HEADER_LEVEL_HEIGHT,
    MIN_COLUMN_WIDTH,
    MIN_HEADER_LEVEL_HEIGHT,
} from "@/features/renovation/renovation-grid-constants";
import type { RenovationField } from "@/shared/api/renovation";

export function clampWidth(width: number): number {
    return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, width));
}

export function clampLevelHeight(height: number): number {
    return Math.min(MAX_HEADER_LEVEL_HEIGHT, Math.max(MIN_HEADER_LEVEL_HEIGHT, height));
}

/** A contiguous slice of a resize handle along its long axis (body coordinates). */
export type HandleBand = { from: number; to: number };

/** Merge touching/overlapping bands — the tiling header cells share their edges. */
export function mergeBands(bands: HandleBand[]): HandleBand[] {
    const sorted = [...bands].sort((a, b) => a.from - b.from);
    const merged: HandleBand[] = [];
    for (const band of sorted) {
        const last = merged[merged.length - 1];
        if (last && band.from <= last.to + 0.5) last.to = Math.max(last.to, band.to);
        else merged.push({ ...band });
    }
    return merged;
}

/**
 * `clip-path` limiting a 6px-thick resize handle to the bands where its
 * boundary line is actually drawn: inside a merged (spanned) header cell there
 * is no line, and the handle must not react to the pointer there — otherwise
 * the resize bar pops up over the merged cells' interior. `null` when the
 * bands cover the whole box (nothing to clip).
 */
export function handleClipPath(
    bands: HandleBand[],
    length: number,
    vertical: boolean,
): string | null {
    // No visible band — hide the handle entirely (defensive: the callers skip
    // such handles, the column handles always carry the number strip's band).
    if (bands.length === 0) return "inset(0 0 100% 0)";
    const first = bands[0];
    const last = bands[bands.length - 1];
    if (
        bands.length === 1 &&
        first !== undefined &&
        last !== undefined &&
        first.from <= 0.5 &&
        last.to >= length - 0.5
    ) {
        return null;
    }
    const px = (value: number): string => `${Math.round(value * 10) / 10}px`;
    const points: string[] = [];
    for (const band of bands) {
        // A "staircase" polygon: disjoint bands, the connectors run along the
        // handle's own edges, so nothing between the bands is painted or hit.
        if (vertical) {
            points.push(`0px ${px(band.from)}`, `6px ${px(band.from)}`);
            points.push(`6px ${px(band.to)}`, `0px ${px(band.to)}`);
        } else {
            points.push(`${px(band.from)} 0px`, `${px(band.from)} 6px`);
            points.push(`${px(band.to)} 6px`, `${px(band.to)} 0px`);
        }
    }
    return `polygon(${points.join(", ")})`;
}

/**
 * The number a column shows in the strip: the workbook ordinal shifted so the
 * first data column («Предприятие») is 1 — the workbook counts the row-number
 * «№» column as 1, the app does not number it. Using the ordinal (not the
 * field's `position`, which the dropped estimate_number skips) keeps the numbering
 * continuous whatever is hidden.
 */
export function columnNumber(field: RenovationField): number {
    return field.ordinal - 1;
}

export function defaultLevelHeights(): number[] {
    return Array.from({ length: HEADER_LEVEL_COUNT }, () => HEADER_LEVEL_HEIGHT);
}

/** Persisted header level heights (a JSON array) with per-level clamping. */
export function loadLevelHeights(): number[] {
    const fallback = defaultLevelHeights();
    // Guard the lazy initializer: it runs during render, which can happen in an
    // environment without `window` (SSR / early hydration / some e2e).
    if (typeof window === "undefined") return fallback;
    try {
        const raw = window.localStorage.getItem(HEADER_LEVEL_HEIGHT_STORAGE_KEY);
        if (!raw) return fallback;
        const parsed: unknown = JSON.parse(raw);
        if (!Array.isArray(parsed) || parsed.length !== HEADER_LEVEL_COUNT) return fallback;
        return parsed.map((value, index) => {
            const numeric = Number(value);
            return Number.isFinite(numeric)
                ? clampLevelHeight(numeric)
                : (fallback[index] ?? HEADER_LEVEL_HEIGHT);
        });
    } catch {
        return fallback;
    }
}

// Kept as a compatibility export for header consumers. The implementation is
// owned by the versioned RevoGrid adapter so shadow-DOM target rules do not
// drift between the two grid engines.
export { isRevoGridTarget as isGridTarget } from "@/shared/grid/revo-grid-adapter";
