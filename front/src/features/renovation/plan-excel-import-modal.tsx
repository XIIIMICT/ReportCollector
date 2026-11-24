"use client";

import { Button, Label, Modal, ProgressBar } from "@heroui/react";
import { Upload } from "iconoir-react";
import { useEffect, useState } from "react";

import type { PlanImportProgress } from "@/shared/api/renovation";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { FileInput } from "@/shared/ui/file-input";

type ExcelCellIssue = {
    location: string;
    value: string;
    /** Human-readable field label (no header hierarchy). */
    column_label?: string | null;
};

export type PlanExcelImportResult =
    | { status: "invalid-format" }
    | {
          status: "has-errors";
          /** Required headings absent from a readable file. */
          missingRequiredColumns?: string[];
          duplicateCodes?: string[];
          invalidCalculatedCodes?: string[];
          invalidDataTypes?: ExcelCellIssue[];
          /** Numeric cells breaking the field's integer/min/max rules. */
          invalidValues?: ExcelCellIssue[];
          /** Excel «#REF!» cached values — a broken formula reference. */
          formulaReferenceErrors?: ExcelCellIssue[];
          /** Required cells left empty in a row — an error of the file. */
          missingRequiredValues?: ExcelCellIssue[];
          missingDictionaryValues?: ExcelCellIssue[];
          unknownAssets?: ExcelCellIssue[];
          /** Rows the load will not write: the asset cell is empty. */
          skippedRows?: number[];
          /** Every data row lacked the asset — there is nothing to load. */
          allRowsSkipped?: boolean;
      }
    | {
          status: "ready";
          assets: string[];
          newRows: number;
          rowsToUpdate: number;
          /** Form labels of the columns the load writes, in workbook order. */
          columns?: string[];
          /** Rows the load will skip because the asset cell is empty. */
          skippedRows?: number[];
      };

/**
 * The preflight dialog. It owns the file check; the detached load itself is
 * reported by the workspace's footer progress bar and settles as a toast.
 */
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
    /**
     * Starts the detached load; the dialog closes at once, and the workspace's
     * footer bar owns the progress from there.
     */
    onUpload?: (file: File) => Promise<void> | void;
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
        if (!file || uploadPending || !onUpload) return;
        const selected = file;
        setUploadPending(true);
        // The load is detached from the dialog at once: the workspace's footer
        // bar carries its progress, so the dialog closes and stops blocking the
        // grid behind it.
        onOpenChange(false);
        try {
            await onUpload(selected);
        } finally {
            setUploadPending(false);
        }
    }

    // The check is ONE request with no server-reported progress: the bar only
    // shows motion while the analysis runs. The load's percentage lives in the
    // footer.
    const statusLabel = phase === "uploading" ? "Идёт загрузка..." : "Идёт анализ...";
    const dragging = isDragging || isPageDragging;
    return (
        <Modal.Backdrop
            isOpen={isOpen}
            onOpenChange={onOpenChange}
            // A stray click on the backdrop must not throw away a checked file
            // or interrupt an upload: the dialog closes only by its own close
            // button, Escape, or the «Загрузить» hand-off.
            isDismissable={false}
            isKeyboardDismissDisabled={false}
        >
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
                                <SkippedRows rows={result.skippedRows} />
                            </div>
                        ) : null}
                        {checkPending ? (
                            <div className="plan-excel-import-modal__middle">
                                <div className="plan-excel-import-modal__status" role="status">
                                    <ProgressBar
                                        aria-label={statusLabel}
                                        className="plan-excel-import-modal__progress"
                                        // The check is one opaque request: there
                                        // is no size or count to state, so the bar
                                        // shows motion, never a fake percentage.
                                        isIndeterminate
                                    >
                                        <Label>{statusLabel}</Label>
                                        <ProgressBar.Track>
                                            <ProgressBar.Fill />
                                        </ProgressBar.Track>
                                    </ProgressBar>
                                </div>
                            </div>
                        ) : (
                            <div className="plan-excel-import-modal__middle">
                                <p
                                    className={`plan-excel-import-modal__hint${dragging ? " plan-excel-import-modal__hint--active" : ""}${hasResult ? " plan-excel-import-modal__hint--with-result" : ""}`}
                                >
                                    {dragging
                                        ? "Перетащите файл в это окно."
                                        : "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа."}
                                </p>
                            </div>
                        )}
                    </Modal.Body>
                    <Modal.Footer className="plan-excel-import-modal__footer">
                        <div className="plan-excel-import-modal__file-row">
                            <FileInput
                                accept=".xlsb,.xlsx,application/vnd.ms-excel.sheet.binary.macroEnabled.12,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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
            {result.missingRequiredColumns?.length ? (
                <section>
                    <strong>Не найдены обязательные столбцы:</strong>
                    <ul>
                        {result.missingRequiredColumns.map((column) => (
                            <li key={column}>{column}</li>
                        ))}
                    </ul>
                </section>
            ) : null}
            {result.duplicateCodes?.length ? (
                <ErrorList title="Дублирующиеся коды" values={result.duplicateCodes} />
            ) : null}
            {/*
                ARCHIVED: the «incorrectly calculated codes» section. The
                renovation-ID formula is off (`RENOVATION_ID_FORMULA_ENABLED` in
                back/src/renovation/renovation_id.py), work_or_repair_code is loaded from the
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
            {result.invalidValues?.length ? (
                <CellErrorList title="Недопустимые значения" values={result.invalidValues} />
            ) : null}
            {result.formulaReferenceErrors?.length ? (
                <CellErrorList
                    title="Ошибки ссылок в формулах"
                    values={result.formulaReferenceErrors}
                />
            ) : null}
            {result.missingRequiredValues?.length ? (
                <CellErrorList
                    title="Не заполнены обязательные значения"
                    values={result.missingRequiredValues}
                />
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
            <SkippedRows
                allRowsSkipped={result.allRowsSkipped ?? false}
                rows={result.skippedRows}
            />
        </div>
    );
}

/**
 * Rows the load will not write because the asset cell is empty, at the end of
 * the analysis — after the summary when the file is valid and after the errors
 * when it is not. A file whose every row lacks the asset has nothing to load,
 * which is a failure of its own.
 */
function SkippedRows({
    rows,
    allRowsSkipped = false,
}: {
    rows: number[] | undefined;
    allRowsSkipped?: boolean;
}) {
    if (!rows?.length) return null;
    return (
        <section className="plan-excel-import-modal__skipped">
            <strong>Пропущенные строки ({rows.length}):</strong>
            {allRowsSkipped ? (
                <p>
                    Во всех строках не заполнено «Наименование ДО» или «Предприятие». Нечего
                    загружать.
                </p>
            ) : (
                <p>{rows.join(", ")}</p>
            )}
        </section>
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

function CellErrorList({ title, values }: { title: string; values: ExcelCellIssue[] | undefined }) {
    if (!values?.length) return null;
    return (
        <section>
            <strong>
                {title} ({values.length}):
            </strong>
            <ul>
                {groupCellIssues(values).map((group) => (
                    <li key={group.column}>
                        {group.label
                            ? `${group.label} (столбец ${group.column}):`
                            : `Столбец ${group.column}:`}
                        <ul>
                            {group.values.map((grouped) => (
                                <li key={`${group.column}-${grouped.value}`}>
                                    {grouped.value || "пусто"} (строки: {grouped.rows.join(", ")})
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
    label: string | null;
    values: Array<{ value: string; rows: number[] }>;
}> {
    const byColumn = new Map<
        string,
        { label: string | null; rowsByValue: Map<string, number[]> }
    >();
    for (const issue of values) {
        const [column, row] = splitCellLocation(issue.location);
        const group = byColumn.get(column) ?? {
            label: issue.column_label ?? null,
            rowsByValue: new Map<string, number[]>(),
        };
        if (group.label === null && issue.column_label) group.label = issue.column_label;
        byColumn.set(column, group);
        const rows = group.rowsByValue.get(issue.value) ?? [];
        rows.push(row);
        group.rowsByValue.set(issue.value, rows);
    }
    return [...byColumn.entries()].map(([column, group]) => ({
        column,
        label: group.label,
        values: [...group.rowsByValue.entries()].map(([value, rows]) => ({
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
