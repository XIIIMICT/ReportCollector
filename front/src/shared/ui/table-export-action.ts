import { toast } from "@heroui/react";

import type { ExportColumn } from "@/shared/grid/grid-config";

import { downloadBlob, triggerNativeDownload } from "./download";
import { errorMessage } from "./text-helpers";

export type GridExportView = { columns: ExportColumn[]; rowIds: string[]; mergeRows?: boolean };
type Download = Blob | { fileUrl: string; job: { fileName: string } };

/** Event-only export flow; ownership and publication of pending state stay local. */
export async function runTableExport({
    pending,
    gridRef,
    setPending,
    fileName,
    request,
}: {
    pending: boolean;
    gridRef: { current: { getExportState: () => Promise<GridExportView | null> } | null };
    setPending: (pending: boolean) => void;
    fileName: () => string;
    request: (view: GridExportView, fileName: string) => Promise<Download>;
}): Promise<void> {
    if (pending) return;
    // Include grid-read failures in the same error/finally path as the request.
    setPending(true);
    try {
        const view = await gridRef.current?.getExportState();
        if (!view) return;
        const name = fileName();
        const result = await request(view, name);
        if (result instanceof Blob) {
            downloadBlob(result, name);
            toast.success("Файл выгружен", { description: name });
        } else {
            triggerNativeDownload(result.fileUrl, result.job.fileName);
            toast.success("Файл формируется", { description: result.job.fileName });
        }
    } catch (reason) {
        toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
    } finally {
        setPending(false);
    }
}
