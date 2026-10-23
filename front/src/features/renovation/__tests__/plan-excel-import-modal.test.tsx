import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";

import { toast } from "@heroui/react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";

import { type PlanExcelUploadResult, PlanExcelImportModal } from "../plan-excel-import-modal";

const HINT = "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.";

const toasts: string[] = [];
// The toast viewport needs a full landmark tree that happy-dom cannot provide,
// so the dialog is checked against the notifications it raises. A spy (not a
// module mock) keeps every other test in the run on the real implementation.
let successSpy: ReturnType<typeof spyOn<typeof toast, "success">> | undefined;

beforeEach(() => {
    toasts.length = 0;
    successSpy = spyOn(toast, "success").mockImplementation(
        ((title: string, options?: { description?: string }) => {
            toasts.push([title, options?.description].filter(Boolean).join(" · "));
            return "toast-id";
        }) as typeof toast.success,
    );
});

afterEach(() => {
    successSpy?.mockRestore();
    cleanup();
});

test("opens the file picker and displays the selected file name", () => {
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
    fireEvent.change(input, { target: { files: [file] } });

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

test("shows the spinner and «Идёт анализ...» while the file is being checked", async () => {
    let finish: (() => void) | undefined;
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() =>
                new Promise<void>((resolve) => {
                    finish = resolve;
                })
            }
            onOpenChange={() => {}}
        />,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
        fireEvent.change(input, { target: { files: [new File(["xlsb"], "plan.xlsb")] } });
    });

    const statuses = Array.from(document.querySelectorAll(".plan-excel-import-modal__status"));
    const status = statuses[statuses.length - 1];
    expect(status?.textContent).toContain("Идёт анализ...");
    expect(status?.querySelector("svg")).toBeTruthy();

    await act(async () => {
        finish?.();
    });
    expect(
        view.getByText(
            "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.",
        ),
    ).toBeTruthy();
});

test("the upload shows the load state, then closes and raises a success toast", async () => {
    const uploaded: File[] = [];
    const opened: boolean[] = [];
    let finish: ((value: PlanExcelUploadResult) => void) | undefined;
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={(open) => opened.push(open)}
            onUpload={(file) => {
                uploaded.push(file);
                return new Promise<PlanExcelUploadResult>((resolve) => {
                    finish = resolve;
                });
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

    // The load is highlighted the same way as the analysis: spinner + label,
    // and the rest of the dialog steps aside while it runs.
    const statuses = Array.from(document.querySelectorAll(".plan-excel-import-modal__status"));
    const status = statuses[statuses.length - 1];
    expect(status?.textContent).toContain("Идёт загрузка в базу...");
    expect(status?.querySelector("svg")).toBeTruthy();
    expect(opened).toEqual([]);

    await act(async () => {
        finish?.({ status: "done", newRows: 10, updatedRows: 30, skippedRows: 2 });
    });

    expect(uploaded).toEqual([file]);
    // Done: the dialog closes for the grid behind it, the summary is a toast.
    expect(opened).toContain(false);
    expect(toasts).toEqual([
        "Файл загружен · Новых строк: 10 · Обновлено строк: 30 · Пропущено (актив вне проекта): 2",
    ]);
    // Nothing is reported inside the dialog any more.
    expect(view.queryByText("Загружено: 40")).toBeNull();
});

test("a failed load keeps the checked file and shows the server message", async () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            onUpload={() => {
                throw new Error("Строка 5: значение «ОКР» не найдено в справочнике.");
            }}
            result={{ status: "ready", assets: ["АЭС-1"], newRows: 1, rowsToUpdate: 0 }}
        />,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
        fireEvent.change(input, { target: { files: [new File(["xlsb"], "plan.xlsb")] } });
    });
    await act(async () => {
        fireEvent.click(view.getByRole("button", { name: "Загрузить" }));
    });

    expect(view.getByRole("alert").textContent).toContain("не найдено в справочнике");
    // The file is still there: the user only has to fix the sheet and re-pick.
    const nameField = document.querySelector(".file-input__text") as HTMLInputElement;
    expect(nameField.value).toBe("plan.xlsb");
});

test("the errors report lists blank required cells", () => {
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{
                status: "has-errors",
                blankValues: [{ location: "Код работы, строка 7", value: "" }],
            }}
        />,
    );
    expect(view.getByText(/Не заполнены обязательные ячейки \(1\)/)).toBeTruthy();
    expect(view.getByText("Столбец Код работы:")).toBeTruthy();
    expect(view.getByText("(строки: 7)", { exact: false })).toBeTruthy();
});
