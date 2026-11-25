import { toast } from "@heroui/react";

import type { ExportColumn } from "@/shared/grid/grid-config";

import { downloadBlob, triggerNativeDownload } from "./download";
import { errorMessage } from "./text-helpers";

export type GridExportView = { columns: ExportColumn[]; rowIds: string[]; mergeRows?: boolean };
type Download =
    | Blob
    | { fileUrl: string; jobsBaseUrl: string; job: { id: string; fileName: string } };

/** Event-only export flow; ownership and publication of pending state stay local. */
export async function runTableExport({
    pending,
    gridRef,
    setPending,
    fileName,
    request,
    onJob,
    signal,
}: {
    pending: boolean;
    gridRef: { current: { getExportState: () => Promise<GridExportView | null> } | null };
    setPending: (pending: boolean) => void;
    fileName: () => string;
    request: (view: GridExportView, fileName: string) => Promise<Download>;
    /** When set, the caller owns job polling/cancellation instead of an immediate native download. */
    onJob?:
        | ((result: {
              fileUrl: string;
              jobsBaseUrl: string;
              job: { id: string; fileName: string };
          }) => void)
        | undefined;
    signal?: AbortSignal | undefined;
}): Promise<void> {
    if (pending) return;
    setPending(true);
    let jobStarted = false;
    try {
        const view = await gridRef.current?.getExportState();
        if (!view) return;
        const name = fileName();
        const result = await request(view, name);
        if (result instanceof Blob) {
            downloadBlob(result, name);
            toast.success("Файл выгружен", { description: name });
        } else if (onJob !== undefined) {
            jobStarted = true;
            onJob(result);
        } else {
            triggerNativeDownload(result.fileUrl, result.job.fileName);
            toast.success("Файл формируется", { description: result.job.fileName });
        }
    } catch (reason) {
        if (signal?.aborted !== true) {
            toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
        }
    } finally {
        if (!jobStarted) setPending(false);
    }
}
