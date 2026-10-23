"use client";

import { Button, Modal, Spinner } from "@heroui/react";
import { Upload } from "iconoir-react";
import { useEffect, useState } from "react";

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
      }
    | {
          status: "ready";
          assets: string[];
          newRows: number;
          rowsToUpdate: number;
      };

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
    onFileSelected: (file: File) => Promise<unknown> | void;
    onUpload?: () => void;
}) {
    const [fileName, setFileName] = useState("");
    const [checkPending, setCheckPending] = useState(false);
    const [checkError, setCheckError] = useState<"invalid-format" | null>(null);
    // A dragged file hovering the dialog (nested elements fire leave/enter, so
    // the state is toggled from `dragover` instead of `dragleave` alone).
    const [isDragging, setIsDragging] = useState(false);
    // A file dragged anywhere over the page — the hint appears before the
    // pointer reaches the dialog, so the destination is obvious.
    const [isPageDragging, setIsPageDragging] = useState(false);
    const hasResult = result !== null;
    // Every open starts clean: a previous file must not leak into a new check.
    useEffect(() => {
        if (isOpen) return;
        setFileName("");
        setCheckPending(false);
        setCheckError(null);
        setIsDragging(false);
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
    const inputError = invalidFormat
        ? "Файл имеет некорректный формат"
        : result?.status === "has-errors"
          ? "Файл содержит ошибки"
          : null;

    async function chooseFile(file: File | undefined) {
        if (!file) return;
        setFileName(file.name);
        setCheckPending(true);
        setCheckError(null);
        try {
            await onFileSelected(file);
        } catch {
            setCheckError("invalid-format");
        } finally {
            setCheckPending(false);
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
                        {checkPending ? (
                            <p className="plan-excel-import-modal__status" role="status">
                                <Spinner size="sm" />
                                Идёт анализ...
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
                            </div>
                        ) : null}
                    </Modal.Body>
                    <Modal.Footer className="plan-excel-import-modal__footer">
                        <div className="plan-excel-import-modal__file-row">
                            <FileInput
                                accept=".xlsb,application/vnd.ms-excel.sheet.binary.macroEnabled.12"
                                error={inputError}
                                fileName={fileName}
                                isDisabled={checkPending}
                                onFileSelected={chooseFile}
                                placeholder="Имя файла"
                            />
                            <Button
                                className="plan-excel-import-modal__upload"
                                isDisabled={result?.status !== "ready"}
                                onPress={() => onUpload?.()}
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
