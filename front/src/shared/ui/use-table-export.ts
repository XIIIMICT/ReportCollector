import { useCallback, useRef, useState } from "react";

import type { ExportColumn } from "@/shared/grid/grid-config";

import { runTableExport, type GridExportView } from "./table-export-action";

/** «вся таблица» — every row of the view; «видимая» — what the grid shows. */
export type ExportMode = "all" | "visible";

/**
 * The request view handed to a call site.
 *
 * `rowIds` is a list for «видимая» and `null` for «вся» — already resolved by
 * the hook, so a call site only forwards it and never re-derives the mode.
 */
export type ExportRequestView = {
    columns: ExportColumn[];
    rowIds: string[] | null;
    mergeRows: boolean;
};

/**
 * One export flow for every grid: pending state, the grid snapshot, the file
 * name and the request.
 *
 * The point is the `rowIds` rule, which used to be retyped at each call site —
 * and one copy of it silently drifted into a stub («forms have no client-side
 * filters»), so «видимая» exported the whole table. Keeping it in ONE place
 * means a form cannot forget it again:
 *
 * - `"visible"` sends `view.rowIds` — the rows the grid currently shows, IN ITS
 *   ORDER. Header filters and sorting run client-side, so the grid is the only
 *   component that knows either; the server must not reimplement them.
 * - `"all"` sends `null` and the server falls back to the whole view.
 *
 * @example
 * const { pending, exportTable } = useTableExport({
 *     gridRef,
 *     fileName: () => exportFileName(section, null),
 *     request: (view, name) => exportKppkTable({ section, columns: view.columns, rowIds: view.rowIds, fileName: name }),
 * });
 */
export function useTableExport({
    gridRef,
    fileName,
    request,
}: {
    gridRef: { current: { getExportState: () => Promise<GridExportView | null> } | null };
    /** The download's file name; receives the mode so a form may distinguish them. */
    fileName: (mode: ExportMode) => string;
    /** Build and perform the request; `view.rowIds` is already mode-resolved. */
    request: (
        view: ExportRequestView,
        fileName: string,
    ) => Promise<Blob | { fileUrl: string; job: { fileName: string } }>;
}): { pending: boolean; exportTable: (mode: ExportMode) => void } {
    const [pending, setPending] = useState(false);
    // `fileName` and `request` are passed INLINE by every call site, so their
    // identity changes on every render of the owning section. If `exportTable`
    // depended on them it would be a new function each render — and the section
    // publishes it (`onExportState({ pending, onExport: exportTable })`) from an
    // effect, the workspace stores the fresh object, re-renders, rebuilds the
    // callback, and the effect fires again: «Maximum update depth exceeded».
    // That is what took the «Предприятия» and «Пользователи» tabs of «Ремонт»
    // down. Read them through refs instead, so this callback is stable for the
    // hook's lifetime.
    const fileNameRef = useRef(fileName);
    fileNameRef.current = fileName;
    const requestRef = useRef(request);
    requestRef.current = request;
    const pendingRef = useRef(pending);
    pendingRef.current = pending;
    const exportTable = useCallback(
        (mode: ExportMode) => {
            void runTableExport({
                pending: pendingRef.current,
                gridRef,
                setPending,
                fileName: () => fileNameRef.current(mode),
                // THE rule: «видимая» = the grid's own rows, in the grid's order.
                request: (view, name) =>
                    requestRef.current(
                        {
                            columns: view.columns,
                            rowIds: mode === "visible" ? view.rowIds : null,
                            mergeRows: view.mergeRows ?? true,
                        },
                        name,
                    ),
            });
        },
        [gridRef],
    );
    return { pending, exportTable };
}
