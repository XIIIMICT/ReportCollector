"use client";

import { Button, Modal, Spinner, toast } from "@heroui/react";
import { Upload } from "iconoir-react";
import { useEffect, useState } from "react";

import type { PlanImportProgress } from "@/shared/api/renovation";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { FileInput } from "@/shared/ui/file-input";

type ExcelCellIssue = {
    location: string;
    value: string;
};

export type PlanExcelImportResult =
    | { status: "invalid-format" }
    | {
          status: "has-errors";
          duplicateCodes?: string[];
          invalidCalculatedCodes?: string[];
          invalidDataTypes?: ExcelCellIssue[];
          missingDictionaryValues?: ExcelCellIssue[];
          unknownAssets?: ExcelCellIssue[];
          blankValues?: ExcelCellIssue[];
      }
    | {
          status: "ready";
          assets: string[];
          newRows: number;
          rowsToUpdate: number;
          /** Form labels of the columns the load writes, in workbook order. */
          columns?: string[];
      };

/** What the dialog shows after the load finished (or failed). */
export type PlanExcelUploadResult =
    | { status: "done"; newRows: number; updatedRows: number }
    | { status: "error"; message: string };

/** The one-line load summary carried by the success toast. */
export function loadSummary(result: Extract<PlanExcelUploadResult, { status: "done" }>): string {
    return [`Новых строк: ${result.newRows}`, `Обновлено строк: ${result.updatedRows}`].join(" · ");
}

export function PlanExcelImportModal({
    isOpen,
    result = null,
    onOpenChange,
    onFileSelected,
    onUpload,
}: {
    isOpen: boolean;
    result?: PlanExcelImportResult | null;
    onOpenChange: (open: boolean) => void;
    onFileSelected: (file: File, progress: PlanImportProgress) => Promise<unknown> | void;
    /** Loads the already checked file; the dialog owns its pending state. */
    onUpload?: (file: File) => Promise<PlanExcelUploadResult> | void;
}) {
    const [fileName, setFileName] = useState("");
    const [checkPending, setCheckPending] = useState(false);
    const [checkError, setCheckError] = useState<"invalid-format" | "too-large" | null>(null);
    // A dragged file hovering the dialog (nested elements fire leave/enter, so
    // the state is toggled from `dragover` instead of `dragleave` alone).
    const [isDragging, setIsDragging] = useState(false);
    // A file dragged anywhere over the page — the hint appears before the
    // pointer reaches the dialog, so the destination is obvious.
    const [isPageDragging, setIsPageDragging] = useState(false);
    // The file itself, kept for the load: the check result says the file is
    // valid, but the load needs the bytes it described.
    const [file, setFile] = useState<File | null>(null);
    const [uploadPending, setUploadPending] = useState(false);
    const [uploadResult, setUploadResult] = useState<PlanExcelUploadResult | null>(null);
    const hasResult = result !== null;
    // Every open starts clean: a previous file must not leak into a new check.
    useEffect(() => {
        if (isOpen) return;
        setFileName("");
        setFile(null);
        setCheckPending(false);
        setCheckError(null);
        setIsDragging(false);
        setUploadPending(false);
        setUploadResult(null);
    }, [isOpen]);
    // Page-wide drag tracking: a drag carries no drop data until it enters a
    // drop target, so only the dialog/body handlers accept the drop itself.
    useEffect(() => {
        if (!isOpen) return;
        const onDragOver = (event: DragEvent) => {
            if (event.dataTransfer?.types.includes("Files")) setIsPageDragging(true);
        };
        const onDragLeave = (event: DragEvent) => {
            // Leaving the window: no relatedTarget left in the document.
            if (event.relatedTarget === null) setIsPageDragging(false);
        };
        const onDrop = () => setIsPageDragging(false);
        const onDragEnd = () => setIsPageDragging(false);
        window.addEventListener("dragover", onDragOver);
        window.addEventListener("dragleave", onDragLeave);
        window.addEventListener("drop", onDrop);
        window.addEventListener("dragend", onDragEnd);
        return () => {
            window.removeEventListener("dragover", onDragOver);
            window.removeEventListener("dragleave", onDragLeave);
            window.removeEventListener("drop", onDrop);
            window.removeEventListener("dragend", onDragEnd);
            setIsPageDragging(false);
        };
    }, [isOpen]);
    const invalidFormat = result?.status === "invalid-format" || checkError === "invalid-format";
    const tooLarge = checkError === "too-large";
    const inputError = tooLarge
        ? "Файл имеет слишком большой размер"
        : invalidFormat
          ? "Файл имеет некорректный формат"
          : result?.status === "has-errors"
            ? "Файл содержит ошибки"
            : null;

    /**
     * Which of the two waits is running. The check is ONE request: the browser
     * uploads the bytes, the server then parses them and answers. The modal
     * cannot see inside the request, so the cut is made where the upload is
     * handed over — that is the part the user can influence (connection speed),
     * and everything after it is server work.
     */
    const [phase, setPhase] = useState<"uploading" | "analyzing" | null>(null);

    async function chooseFile(selected: File | undefined) {
        if (!selected) return;
        setFile(selected);
        setFileName(selected.name);
        setCheckPending(true);
        setCheckError(null);
        setUploadResult(null);
        // The request is ONE round trip: the browser streams the bytes, then the
        // server parses them and answers. `onFileSelected` cannot be told apart
        // from the outside, so the phase is switched at the first sign of the
        // server working — the moment the request has been handed over.
        setPhase("uploading");
        try {
            await onFileSelected(selected, {
                uploading: () => setPhase("uploading"),
                analyzing: () => setPhase("analyzing"),
            });
        } catch (error) {
            // A 413 is the proxy refusing the upload, not a broken workbook: the
            // user has to be told to shrink the file, not to fix its contents.
            setCheckError(
                error instanceof ReportCollectorApiError && error.status === 413
                    ? "too-large"
                    : "invalid-format",
            );
        } finally {
            setCheckPending(false);
            setPhase(null);
        }
    }

    async function upload() {
        if (!file || uploadPending) return;
        setUploadPending(true);
        try {
            const outcome = await onUpload?.(file);
            if (!outcome) return;
            if (outcome.status === "done") {
                // A finished load needs no dialog: the summary moves to a toast
                // and the grid behind it refetches the loaded rows.
                toast.success("Файл загружен", { description: loadSummary(outcome) });
                onOpenChange(false);
                return;
            }
            setUploadResult(outcome);
        } catch (error) {
            setUploadResult({
                status: "error",
                message: error instanceof Error ? error.message : "Не удалось загрузить файл.",
            });
        } finally {
            setUploadPending(false);
        }
    }

    const dragging = isDragging || isPageDragging;
    return (
        <Modal.Backdrop isOpen={isOpen} onOpenChange={onOpenChange}>
            <Modal.Container size="lg">
                <Modal.Dialog className="plan-excel-import-modal">
                    <Modal.CloseTrigger />
                    <Modal.Header>
                        <Modal.Heading>Загрузка данных технической дирекции из Excel</Modal.Heading>
                    </Modal.Header>
                    <Modal.Body
                        className="plan-excel-import-modal__body"
                        onDragEnter={(event) => {
                            event.preventDefault();
                            setIsDragging(true);
                            setIsPageDragging(true);
                        }}
                        onDragOver={(event) => {
                            // Without preventDefault the browser opens the file.
                            event.preventDefault();
                            event.dataTransfer.dropEffect = "copy";
                            setIsDragging(true);
                            setIsPageDragging(true);
                        }}
                        onDragLeave={(event) => {
                            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                                setIsDragging(false);
                            }
                        }}
                        onDrop={(event) => {
                            event.preventDefault();
                            setIsDragging(false);
                            setIsPageDragging(false);
                            void chooseFile(event.dataTransfer.files[0]);
                        }}
                    >
                        {checkPending || uploadPending ? (
                            <p className="plan-excel-import-modal__status" role="status">
                                <Spinner size="sm" />
                                {checkPending
                                    ? phase === "uploading"
                                        ? "Идёт загрузка..."
                                        : "Идёт анализ..."
                                    : "Идёт загрузка в базу..."}
                            </p>
                        ) : (
                            <p
                                className={`plan-excel-import-modal__hint${dragging ? " plan-excel-import-modal__hint--active" : ""}${hasResult ? " plan-excel-import-modal__hint--with-result" : ""}`}
                            >
                                {dragging
                                    ? "Перетащите файл в это окно."
                                    : "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа."}
                            </p>
                        )}
                        {result?.status === "has-errors" ? <ImportErrors result={result} /> : null}
                        {uploadResult?.status === "error" ? (
                            <p className="plan-excel-import-modal__upload-error" role="alert">
                                {uploadResult.message}
                            </p>
                        ) : null}
                        {result?.status === "ready" ? (
                            <div className="plan-excel-import-modal__summary">
                                <p>
                                    <strong>Активы: {result.assets.join(", ")}</strong>
                                </p>
                                <p>
                                    <strong>Новых строк: {result.newRows}</strong>
                                </p>
                                <p>
                                    <strong>Строк для обновления: {result.rowsToUpdate}</strong>
                                </p>
                                {result.columns?.length ? (
                                    <div className="plan-excel-import-modal__columns">
                                        <p>
                                            <strong>Столбцы для загрузки:</strong>
                                        </p>
                                        <ul>
                                            {result.columns.map((column) => (
                                                <li key={column}>{column}</li>
                                            ))}
                                        </ul>
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                    </Modal.Body>
                    <Modal.Footer className="plan-excel-import-modal__footer">
                        <div className="plan-excel-import-modal__file-row">
                            <FileInput
                                accept=".xlsb,application/vnd.ms-excel.sheet.binary.macroEnabled.12"
                                error={inputError}
                                fileName={fileName}
                                isDisabled={checkPending || uploadPending}
                                onFileSelected={chooseFile}
                                placeholder="Имя файла"
                            />
                            <Button
                                className="plan-excel-import-modal__upload"
                                isDisabled={result?.status !== "ready" || uploadPending || !file}
                                onPress={() => void upload()}
                                variant="primary"
                            >
                                <Upload aria-hidden="true" />
                                Загрузить
                            </Button>
                        </div>
                    </Modal.Footer>
                </Modal.Dialog>
            </Modal.Container>
        </Modal.Backdrop>
    );
}

function ImportErrors({
    result,
}: {
    result: Extract<PlanExcelImportResult, { status: "has-errors" }>;
}) {
    return (
        <div className="plan-excel-import-modal__errors" role="alert">
            {result.duplicateCodes?.length ? (
                <ErrorList title="Дублирующиеся коды" values={result.duplicateCodes} />
            ) : null}
            {/*
                ARCHIVED: the «incorrectly calculated codes» section. The
                renovation-ID formula is off (`RENOVATION_ID_FORMULA_ENABLED` in
                back/src/renovation/renovation_id.py), col_003 is loaded from the
                template as-is, so nothing computes a code to compare against.
                The markup stays for the day the formula returns; the field is
                still typed, so the render below never fires today.
            */}
            {result.invalidCalculatedCodes?.length ? (
                <ErrorList
                    title="Некорректно рассчитанные коды"
                    values={result.invalidCalculatedCodes}
                />
            ) : null}
            {result.invalidDataTypes?.length ? (
                <CellErrorList title="Неверный тип данных" values={result.invalidDataTypes} />
            ) : null}
            {result.missingDictionaryValues?.length ? (
                <CellErrorList
                    title="Значение отсутствует в справочнике"
                    values={result.missingDictionaryValues}
                />
            ) : null}
            {result.unknownAssets?.length ? (
                <CellErrorList
                    title="Актив отсутствует в справочнике"
                    values={result.unknownAssets}
                />
            ) : null}
            {result.blankValues?.length ? (
                <CellErrorList
                    title="Не заполнены обязательные ячейки"
                    values={result.blankValues}
                />
            ) : null}
        </div>
    );
}

function ErrorList({ title, values }: { title: string; values: string[] }) {
    return (
        <section>
            <strong>
                {title} ({values.length}):
            </strong>
            <ul>
                {values.map((value, index) => (
                    <li key={`${value}-${index}`}>{value}</li>
                ))}
            </ul>
        </section>
    );
}

function CellErrorList({ title, values }: { title: string; values: ExcelCellIssue[] }) {
    return (
        <section>
            <strong>
                {title} ({values.length}):
            </strong>
            <ul>
                {groupCellIssues(values).map((group) => (
                    <li key={group.column}>
                        Столбец {group.column}:
                        <ul>
                            {group.values.map((grouped) => (
                                <li key={`${group.column}-${grouped.value}`}>
                                    {grouped.value} (строки: {grouped.rows.join(", ")})
                                </li>
                            ))}
                        </ul>
                    </li>
                ))}
            </ul>
        </section>
    );
}

/**
 * Group cell issues by column, then by value: one nested line per distinct
 * value carrying all the rows it appears on, in sheet order.
 */
function groupCellIssues(values: ExcelCellIssue[]): Array<{
    column: string;
    values: Array<{ value: string; rows: number[] }>;
}> {
    const byColumn = new Map<string, Map<string, number[]>>();
    for (const issue of values) {
        const [column, row] = splitCellLocation(issue.location);
        const rowsByValue = byColumn.get(column) ?? new Map<string, number[]>();
        byColumn.set(column, rowsByValue);
        const rows = rowsByValue.get(issue.value) ?? [];
        rows.push(row);
        rowsByValue.set(issue.value, rows);
    }
    return [...byColumn.entries()].map(([column, rowsByValue]) => ({
        column,
        values: [...rowsByValue.entries()].map(([value, rows]) => ({
            value,
            rows: [...rows].sort((left, right) => left - right),
        })),
    }));
}

/** Split «Z, строка 7» into its column letter(s) and the sheet row number. */
function splitCellLocation(location: string): [string, number] {
    const comma = location.lastIndexOf(",");
    const column = (comma === -1 ? location : location.slice(0, comma)).trim();
    const digits = location.slice(comma + 1).replace(/\D+/g, "");
    return [column, digits === "" ? 0 : Number(digits)];
}
