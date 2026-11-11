/**
 * Constants and small tables of the renovation plan grid.
 *
 * Split out of `renovation-grid.tsx` so the layout, width and header numbers
 * live in one readable place instead of being interleaved with the rendering
 * logic. Nothing here has behaviour.
 */

import { SERVICE_DESCRIPTORS } from "@/shared/grid/grid-config";

/** The plan-row table name (the collaboration/journal target). */
export const RENOVATION_PLAN_ROWS_TABLE = "r_plan_rows";

export const HEADER_LEVEL_HEIGHT = 24;
/** Limits of a header level's height (dragged on the level's bottom boundary). */
export const MIN_HEADER_LEVEL_HEIGHT = 12;
export const MAX_HEADER_LEVEL_HEIGHT = 400;
/** Persisted header level heights — a JSON array, one entry per level. */
export const HEADER_LEVEL_HEIGHT_STORAGE_KEY = "renovation:headerLevelHeights";

export const HEADER_LEVEL_COUNT = 11;
export const ROW_HEADER_WIDTH = 52;

export const MIN_COLUMN_WIDTH = 60;
export const MAX_COLUMN_WIDTH = 600;
export const WIDTH_STORAGE_PREFIX = "renovation:colWidth:";
export const DEFAULT_COLUMN_WIDTH: Record<string, number> = {
    number: 110,
    date: 130,
    boolean: 90,
    reference: 160,
    text: 200,
};

/** The synthetic first column showing the row's asset; an asset picker in the placeholder row. */
export const ASSET_COLUMN_PROP = "__assetName";
export const ASSET_COLUMN_WIDTH = 220;
/** The form field the synthetic asset column replaces (its ordinal numbers the column). */
export const ASSET_FIELD_DB_NAME = "col_002";

/** Placeholder a missing renovation-id component is replaced with (backend renovation_id.py). */
export const MISSING_COMPONENT = "<NULL>";

/**
 * Rows per remote chunk. One scroll load = one request; the plugin keeps
 * `bufferSize` rows around the viewport and evicts the rest, so memory stays
 * flat no matter how far the user scrolls.
 */
export const INFINITY_CHUNK_ROWS = 100;
/** Rows kept loaded around the viewport (chunks are evicted beyond it). */
export const INFINITY_BUFFER_ROWS = 400;
/** Fraction of the last loaded chunk that triggers the next prefetch. */
export const INFINITY_PRELOAD_THRESHOLD = 0.5;

/** Canonical responsibility-block labels — must match the «Все блоки» filter. */
export const BLOCK_HEADER_LABELS: Record<string, string> = {
    tech: "1) Техническая дирекция",
    ksir: "2) Дирекция КСиР",
    mto: "3) Дирекция МТО",
};

/** Service columns revealed by the header's «+»: the same audit trio as every
 * other dictionary (creation time, edit time and the last editor) — not the
 * internal id/position/revision. */
export const SERVICE_COLUMNS: ReadonlyArray<{ dbName: string; label: string; width: number }> =
    SERVICE_DESCRIPTORS.map((descriptor) => ({
        dbName: descriptor.key,
        label: descriptor.label,
        width: descriptor.width,
    }));

/**
 * The audit columns the corner menu reveals, keyed by their row prop: not form
 * fields, so neither the workbook nor `meta.fields` describes them. `kind` is the
 * cell type the grid renders and the popover searches by — instants keep their
 * time (a filter over them asks for a moment), the editor is plain text.
 */
export const AUDIT_COLUMNS: Record<string, { label: string; kind: "date" | "text" }> = {
    createdAt: { label: "Время добавления", kind: "date" },
    updatedAt: { label: "Время редактирования", kind: "date" },
    updatedBy: { label: "Автор последней редакции", kind: "text" },
};

/**
 * The audit props whose cells are INSTANTS (not plan form fields): they render
 * through the shared datetime formatter and their header filter asks for a
 * moment, not for a value list derived from a text column.
 */
export const SERVICE_CREATED_AT = "createdAt";
export const SERVICE_UPDATED_AT = "updatedAt";
export const SERVICE_AUDIT_INSTANT_PROPS = new Set([SERVICE_CREATED_AT, SERVICE_UPDATED_AT]);

/** Row-model fields that are not plan values (the overlay never renders them). */
export const SERVICE_DB_NAMES = new Set(SERVICE_COLUMNS.map((column) => column.dbName));
