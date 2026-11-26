/**
 * Pure header-chain/level derivations for the renovation plan's 11-level
 * overlay. Split out of `renovation-grid.tsx`: these functions only read the
 * form metadata (`headerCells`, `fields`) and the already-computed overlay cell
 * descriptors. They touch no grid instance, ref, DOM or React state, so the
 * component's memoized call sites keep their identities and dependency lists.
 */

import {
    ASSET_COLUMN_PROP,
    BLOCK_HEADER_LABELS,
    SERVICE_DB_NAMES,
} from "@/features/renovation/renovation-grid-constants";
import type { RenovationField, RenovationHeaderCell } from "@/shared/api/renovation";

/**
 * The slice of an overlay cell the chain helpers need. The full overlay cell
 * descriptor from `renovation-grid.tsx` is structurally compatible; keeping
 * this type narrow makes the helpers easy to test in isolation.
 */
export type HeaderSpanCell = {
    text: string;
    level: number;
    fromPosition: number;
    toPosition: number;
};

/**
 * Header chain of a form position, from the RAW header cells — the source of
 * truth for a hidden column, which has no visible overlay cell. Canonical block
 * labels are applied the same way the overlay applies them; consecutive
 * duplicate texts are dropped.
 */
export function buildHeaderChainForPosition(
    position: number,
    headerCells: readonly RenovationHeaderCell[],
    fields: readonly RenovationField[],
): string[] {
    const covered = headerCells
        .filter(
            (cell) =>
                cell.startPosition + 1 <= position &&
                position < cell.startPosition + cell.colSpan + 1,
        )
        .sort((left, right) => left.level - right.level);
    const texts: string[] = [];
    for (const cell of covered) {
        let text = cell.text.trim();
        if (cell.level === 0 && text.startsWith("Блок ")) {
            for (
                let candidatePosition = cell.startPosition + 1;
                candidatePosition < cell.startPosition + cell.colSpan + 1;
                candidatePosition += 1
            ) {
                const field = fields.find(
                    (candidate) =>
                        candidate.position === candidatePosition &&
                        candidate.responsibilityBlock &&
                        candidate.responsibilityBlock !== "read_only",
                );
                if (field?.responsibilityBlock) {
                    text = BLOCK_HEADER_LABELS[field.responsibilityBlock] ?? text;
                    break;
                }
            }
        }
        if (text !== "" && text !== texts[texts.length - 1]) texts.push(text);
    }
    return texts;
}

/**
 * Header chain of every numbered column (the column-number tooltip's lines),
 * from the VISIBLE overlay cells. Service columns carry no workbook header, so
 * their chain is just the column label.
 */
export function buildHeaderChainByProp(
    overlayCells: readonly HeaderSpanCell[],
    assetPosition: number | null,
    displayFields: readonly RenovationField[],
): Map<string, string[]> {
    const chains = new Map<string, string[]>();
    const add = (prop: string, position: number | null) => {
        if (position === null) return;
        const texts: string[] = [];
        for (const cell of overlayCells) {
            if (cell.fromPosition > position || position >= cell.toPosition) continue;
            const text = cell.text.trim();
            // Consecutive duplicates (a label repeated on two levels) read as
            // noise in the chain.
            if (text !== "" && text !== texts[texts.length - 1]) texts.push(text);
        }
        if (texts.length > 0) chains.set(prop, texts);
    };
    add(ASSET_COLUMN_PROP, assetPosition);
    for (const field of displayFields) {
        if (SERVICE_DB_NAMES.has(field.dbName)) {
            // An audit column has no header cell of its own: its chain is the
            // column's label, which is what the tooltip should read.
            chains.set(field.dbName, [field.label]);
            continue;
        }
        add(field.dbName, field.position);
    }
    return chains;
}

/** Highest header level covering each form position (from the overlay cells). */
export function buildHeaderLevelByPosition(
    overlayCells: readonly HeaderSpanCell[],
): Map<number, number> {
    const levels = new Map<number, number>();
    for (const cell of overlayCells) {
        for (let position = cell.fromPosition; position < cell.toPosition; position += 1) {
            levels.set(position, Math.max(levels.get(position) ?? 0, cell.level));
        }
    }
    return levels;
}
