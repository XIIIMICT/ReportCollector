import { afterEach, expect, test } from "bun:test";

import { act, cleanup, fireEvent, render } from "@testing-library/react";

import { PlanExcelImportModal } from "../plan-excel-import-modal";

const HINT = "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.";

afterEach(cleanup);

test("opens the file picker and displays the selected file name", async () => {
    const selected: File[] = [];
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={(file) => {
                selected.push(file);
            }}
            onOpenChange={() => {}}
        />,
    );
    const file = new File(["xlsb"], "plan.xlsb", {
        type: "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    // The check ends in a microtask (`finally` clears the pending state), so the
    // change must be awaited inside act or React reports the late state update.
    await act(async () => {
        fireEvent.change(input, { target: { files: [file] } });
    });

    expect(selected).toEqual([file]);
    expect((view.getByRole("textbox", { name: "Имя файла" }) as HTMLInputElement).value).toBe(
        "plan.xlsb",
    );
});

test("keeps the picker and the upload button in one disabled-until-ready row", () => {
    const view = render(
        <PlanExcelImportModal isOpen onFileSelected={() => {}} onOpenChange={() => {}} />,
    );
    const upload = view.getByRole("button", { name: "Загрузить" });
    const choose = view.getByRole("button", { name: "Выбрать файл" });
    expect(upload.hasAttribute("disabled")).toBe(true);
    expect(choose.className).toContain("button--tertiary");
    expect(choose.className).toContain("expandable-button");
    expect(upload.closest(".plan-excel-import-modal__file-row")).toBe(
        choose.closest(".plan-excel-import-modal__file-row"),
    );
});

test("renders only the supplied error categories", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                duplicateCodes: ["R-1", "R-2"],
                invalidDataTypes: [{ location: "AA, строка 3", value: "abc" }],
                missingDictionaryValues: [
                    { location: "Z, строка 7", value: "ОКР" },
                    { location: "Z, строка 2", value: "ОКР" },
                    { location: "Z, строка 5", value: "ДО" },
                ],
            }}
        />,
    );

    expect(view.getByText("Файл содержит ошибки")).toBeTruthy();
    expect(view.getByText("Дублирующиеся коды (2):")).toBeTruthy();
    expect(view.getByText("R-1")).toBeTruthy();
    expect(view.getByText("Неверный тип данных (1):")).toBeTruthy();
    expect(view.getByText("Столбец AA:")).toBeTruthy();
    expect(view.getByText("abc (строки: 3)")).toBeTruthy();
    expect(view.getByText("Значение отсутствует в справочнике (3):")).toBeTruthy();
    expect(view.getByText("Столбец Z:")).toBeTruthy();
    // Same value on several rows → one line, rows ascending.
    expect(view.getByText("ОКР (строки: 2, 7)")).toBeTruthy();
    expect(view.getByText("ДО (строки: 5)")).toBeTruthy();
    expect(view.queryByText(/Некорректно рассчитанные коды/)).toBeNull();
});

test("a readable file without required columns lists every missing heading", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                missingRequiredColumns: [
                    "Код работы",
                    "Наименование ДО или Предприятие",
                    "Дата начала работ",
                ],
            }}
        />,
    );

    // The file opened: this is a content error, not a broken container.
    expect(view.getByText("Файл содержит ошибки")).toBeTruthy();
    expect(view.queryByText("Файл имеет некорректный формат")).toBeNull();
    expect(view.getByText("Не найдены обязательные столбцы:")).toBeTruthy();
    expect(view.getByText("Код работы")).toBeTruthy();
    expect(view.getByText("Наименование ДО или Предприятие")).toBeTruthy();
    expect(view.getByText("Дата начала работ")).toBeTruthy();
});

test("only an unreadable file reports an incorrect format", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{ status: "invalid-format" }}
        />,
    );

    expect(view.getByText("Файл имеет некорректный формат")).toBeTruthy();
    expect(view.queryByText("Файл содержит ошибки")).toBeNull();
});

test("blank required values are errors, listed by column and row", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                missingRequiredValues: [
                    { location: "O, строка 3", value: "" },
                    { location: "O, строка 5", value: "" },
                ],
            }}
        />,
    );

    expect(view.getByText("Файл содержит ошибки")).toBeTruthy();
    expect(view.getByText("Не заполнены обязательные значения (2):")).toBeTruthy();
    expect(view.getByText("Столбец O:")).toBeTruthy();
    expect(view.getByText("пусто (строки: 3, 5)")).toBeTruthy();
});

test("drops a dragged file into the same picker and shows the analysis state", async () => {
    const selected: File[] = [];
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={(file) => {
                selected.push(file);
            }}
            onOpenChange={() => {}}
        />,
    );
    const dialog = view.getByRole("dialog");
    expect(dialog).toBeTruthy();
    const hint = "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.";
    expect(view.getByText(hint)).toBeTruthy();

    // A drag over the dialog body switches the hint.
    const dropZone = document.querySelector("[data-slot='modal-body']");
    expect(dropZone).not.toBeNull();
    await act(async () => {
        fireEvent.dragOver(dropZone as Element, {
            dataTransfer: { files: [], dropEffect: "", types: ["Files"] },
        });
    });
    expect(view.getByText("Перетащите файл в это окно.")).toBeTruthy();

    const file = new File(["xlsb"], "plan.xlsb");
    await act(async () => {
        fireEvent.drop(dropZone as Element, { dataTransfer: { files: [file] } });
    });

    expect(selected).toEqual([file]);
    expect(view.getByText(hint)).toBeTruthy();
    expect((view.getByRole("textbox", { name: "Имя файла" }) as HTMLInputElement).value).toBe(
        "plan.xlsb",
    );
});

test("the drop hint appears while a file is dragged anywhere over the page", async () => {
    const view = render(
        <PlanExcelImportModal isOpen onFileSelected={() => {}} onOpenChange={() => {}} />,
    );

    // Outside the dialog: the page-level drag still advertises the drop target.
    await act(async () => {
        fireEvent.dragOver(document.body, {
            dataTransfer: { files: [], dropEffect: "", types: ["Files"] },
        });
    });
    expect(view.getByText("Перетащите файл в это окно.")).toBeTruthy();

    // Dropping anywhere ends the drag → back to the standing guidance.
    await act(async () => {
        fireEvent.drop(document.body, { dataTransfer: { files: [] } });
    });
    expect(view.getByText(HINT)).toBeTruthy();
});

test("the guidance disappears once a result is shown", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{ status: "ready", assets: ["АЭС-1"], newRows: 1, rowsToUpdate: 0 }}
        />,
    );
    const hint = view.getByText(
        "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.",
    );
    expect(hint.className).toContain("plan-excel-import-modal__hint--with-result");
    // A new drag brings the drop guidance back, even over an existing report.
    const dropZone = document.querySelector("[data-slot='modal-body']");
    fireEvent.dragOver(dropZone as Element, {
        dataTransfer: { files: [], dropEffect: "", types: ["Files"] },
    });
    const dropHint = view.getByText("Перетащите файл в это окно.");
    expect(dropHint.className).toContain("plan-excel-import-modal__hint--active");
});

test("says «Идёт загрузка...» first, then «Идёт анализ...»", async () => {
    // Two waits, two captions: sending the bytes is the user's connection, the
    // parsing after it is the server's work. The modal tells them apart so the
    // user knows which half of the time is which.
    let finish: (() => void) | undefined;
    let started: { analyzing: () => void } | undefined;
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={(_file, progress) => {
                started = progress;
                return new Promise<void>((resolve) => {
                    finish = resolve;
                });
            }}
            onOpenChange={() => {}}
        />,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const status = () => {
        const all = Array.from(document.querySelectorAll(".plan-excel-import-modal__status"));
        return all[all.length - 1];
    };

    await act(async () => {
        fireEvent.change(input, { target: { files: [new File(["xlsb"], "plan.xlsb")] } });
    });

    // The bytes are still on their way.
    expect(status()?.textContent).toContain("Идёт загрузка...");
    expect(status()?.querySelector('[role="progressbar"]')).toBeTruthy();

    // The upload is done; the server is parsing.
    await act(async () => {
        started?.analyzing();
    });
    expect(status()?.textContent).toContain("Идёт анализ...");

    await act(async () => {
        finish?.();
    });
    expect(
        view.getByText(
            "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.",
        ),
    ).toBeTruthy();
});

test("the upload closes the dialog at once and hands the file to the caller", async () => {
    const uploaded: File[] = [];
    const opened: boolean[] = [];
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={(open) => opened.push(open)}
            onUpload={(file) => {
                uploaded.push(file);
            }}
            result={{ status: "ready", assets: ["АЭС-1", "АЭС-2"], newRows: 10, rowsToUpdate: 30 }}
        />,
    );
    // Nothing is loaded before a file is picked and checked.
    const button = view.getByRole("button", { name: "Загрузить" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(view.getByText("Активы: АЭС-1, АЭС-2")).toBeTruthy();

    const file = new File(["xlsb"], "plan.xlsb");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
        fireEvent.change(input, { target: { files: [file] } });
    });
    await act(async () => {
        fireEvent.click(view.getByRole("button", { name: "Загрузить" }));
    });

    // The dialog steps aside at once: the workspace's footer bar owns the
    // detached load, so the grid behind it stays usable.
    expect(opened).toContain(false);
    expect(uploaded).toEqual([file]);
});

test("the errors report lists the rows skipped for a blank required cell", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                duplicateCodes: ["Р-001 (строки 7, 9)"],
                skippedRows: [7, 9],
            }}
        />,
    );
    expect(view.getByText("Пропущенные строки (2):")).toBeTruthy();
    expect(view.getByText("7, 9")).toBeTruthy();
});

test("a successful analysis also lists the rows it will skip", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "ready",
                assets: ["АЭС-1"],
                newRows: 10,
                rowsToUpdate: 2,
                skippedRows: [4],
            }}
        />,
    );
    expect(view.getByText("Пропущенные строки (1):")).toBeTruthy();
    expect(view.getByText("4")).toBeTruthy();
});

test("a file with every row skipped says there is nothing to load", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{ status: "has-errors", skippedRows: [3, 4], allRowsSkipped: true }}
        />,
    );
    expect(
        view.getByText(
            "Во всех строках не заполнено «Наименование ДО» или «Предприятие». Нечего загружать.",
        ),
    ).toBeTruthy();
    expect(view.queryByText("3, 4")).toBeNull();
});

test("an asset outside the project dictionary is an error of the file", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                // The asset is a reference value like any catalog label.
                unknownAssets: [
                    { location: "C, строка 12", value: "ZZZR" },
                    { location: "C, строка 40", value: "ZZZR" },
                ],
            }}
        />,
    );
    expect(view.getByText("Файл содержит ошибки")).toBeTruthy();
    expect(view.getByText("Актив отсутствует в справочнике (2):")).toBeTruthy();
    expect(view.getByText("Столбец C:")).toBeTruthy();
    expect(view.getByText("ZZZR (строки: 12, 40)")).toBeTruthy();
});

test("cell errors name the field and keep the Excel column letter", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                unknownAssets: [
                    {
                        location: "C, строка 12",
                        value: "ZZZR",
                        column_label: "Наименование ДО",
                    },
                ],
            }}
        />,
    );
    expect(view.getByText("Наименование ДО (столбец C):")).toBeTruthy();
});

test("numeric rule violations are their own category with the field label", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                invalidValues: [
                    {
                        location: "Q, строка 3",
                        value: "150",
                        column_label: "Централизованная поставка",
                    },
                ],
            }}
        />,
    );
    expect(view.getByText("Недопустимые значения (1):")).toBeTruthy();
    expect(view.getByText("Централизованная поставка (столбец Q):")).toBeTruthy();
});

test("broken formula references are reported as their own error category", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                formulaReferenceErrors: [{ location: "Q, строка 3", value: "#REF!" }],
            }}
        />,
    );
    expect(view.getByText("Ошибки ссылок в формулах (1):")).toBeTruthy();
    expect(view.getByText("Столбец Q:")).toBeTruthy();
    expect(view.getByText("#REF! (строки: 3)")).toBeTruthy();
});
