import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { PlanExcelImportModal } from "../plan-excel-import-modal";

afterEach(cleanup);

/**
 * The analysis summary of a ready file. Two details matter to the operator and
 * were both wrong once:
 *  - the entries read as ONE block (a per-row gap looked like stray blank lines
 *    between «Активы», «Новых строк» and the rest);
 *  - the columns the load is going to write are listed, so the result can be
 *    checked against the workbook before pressing «Загрузить».
 */

function renderReady(result: {
    assets: string[];
    newRows: number;
    rowsToUpdate: number;
    columns?: string[];
}) {
    return render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={() => {}}
            result={{ status: "ready", ...result }}
        />,
    );
}

test("the summary lists the counts without a spacer between them", () => {
    const view = renderReady({
        assets: ["АО «БСЗ»", "АО «СНХЗ»"],
        newRows: 498,
        rowsToUpdate: 12,
        columns: ["Цех"],
    });

    const summary = document.querySelector(".plan-excel-import-modal__summary");
    expect(summary).not.toBeNull();
    // The counts are DIRECT children of the single container — no wrapper between
    // them that could re-introduce a gap.
    const counts = Array.from(summary?.children ?? []).filter((child) => child.tagName === "P");
    expect(counts).toHaveLength(3);
    expect(view.getByText(/АО «БСЗ», АО «СНХЗ»/)).toBeTruthy();
    expect(view.getByText(/Новых строк: 498/)).toBeTruthy();
    expect(view.getByText(/Строк для обновления: 12/)).toBeTruthy();
});

test("the columns to be loaded are listed as a bulleted list", () => {
    const view = renderReady({
        assets: ["АО «БСЗ»"],
        newRows: 1,
        rowsToUpdate: 0,
        columns: ["Предприятие", "Цех", "Наименование ЕО"],
    });

    const list = document.querySelector(".plan-excel-import-modal__columns ul");
    expect(list).not.toBeNull();
    const items = Array.from(list?.querySelectorAll("li") ?? []).map((item) => item.textContent);
    expect(items).toEqual(["Предприятие", "Цех", "Наименование ЕО"]);
    expect(view.getByText("Столбцы для загрузки:")).toBeTruthy();
});

test("no column block is rendered when the analyser reports none", () => {
    renderReady({ assets: ["АО «БСЗ»"], newRows: 1, rowsToUpdate: 0 });

    expect(document.querySelector(".plan-excel-import-modal__columns")).toBeNull();
});
