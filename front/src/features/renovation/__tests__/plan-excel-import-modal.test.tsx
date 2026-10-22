import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render } from "@testing-library/react";

import { PlanExcelImportModal } from "../plan-excel-import-modal";

afterEach(cleanup);

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

test("shows the successful summary and upload action", () => {
    let uploaded = false;
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            onUpload={() => {
                uploaded = true;
            }}
            result={{ status: "ready", assets: ["АЭС-1", "АЭС-2"], newRows: 10, rowsToUpdate: 30 }}
        />,
    );

    fireEvent.click(view.getByRole("button", { name: "Загрузить" }));
    expect(uploaded).toBe(true);
    expect(view.getByText("Активы: АЭС-1, АЭС-2")).toBeTruthy();
    expect(view.getByText("Новых строк: 10")).toBeTruthy();
    expect(view.getByText("Строк для обновления: 30")).toBeTruthy();
});
