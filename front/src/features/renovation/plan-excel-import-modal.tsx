"use client";

import { Button, Modal } from "@heroui/react";
import { Upload } from "iconoir-react";
import { useEffect, useState } from "react";

import { FileInput } from "@/shared/ui/file-input";

export type ExcelCellIssue = {
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
    // Every open starts clean: a previous file must not leak into a new check.
    useEffect(() => {
        if (isOpen) return;
        setFileName("");
        setCheckPending(false);
        setCheckError(null);
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

    return (
        <Modal.Backdrop isOpen={isOpen} onOpenChange={onOpenChange}>
            <Modal.Container size="lg">
                <Modal.Dialog className="plan-excel-import-modal">
                    <Modal.CloseTrigger />
                    <Modal.Header>
                        <Modal.Heading>Загрузка данных технической дирекции из Excel</Modal.Heading>
                    </Modal.Header>
                    <Modal.Body className="plan-excel-import-modal__body">
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
                    prefixLocation
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

function CellErrorList({
    title,
    values,
    prefixLocation = false,
}: {
    title: string;
    values: ExcelCellIssue[];
    /** Lead with «Столбец» (catalog misses) rather than angle-bracket quoting. */
    prefixLocation?: boolean;
}) {
    return (
        <section>
            <strong>
                {title} ({values.length}):
            </strong>
            <ul>
                {values.map((issue, index) => (
                    <li key={`${issue.location}-${index}`}>
                        {prefixLocation
                            ? `Столбец ${issue.location}: ${issue.value}`
                            : `<${issue.location}>: ${issue.value}`}
                    </li>
                ))}
            </ul>
        </section>
    );
}
