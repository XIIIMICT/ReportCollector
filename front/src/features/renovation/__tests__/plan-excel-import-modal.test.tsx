import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";

import { toast, toastQueue } from "@heroui/react";
import type { HeroUIToastOptions, ToastContentValue } from "@heroui/react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import type { ReactNode } from "react";

import type { PlanImportJobState } from "@/shared/api/renovation";

import { type PlanExcelUploadResult, PlanExcelImportModal } from "../plan-excel-import-modal";

const HINT = "Выберите файл для загрузки в базу. Здесь будут выведены результаты его анализа.";

type CapturedToast = {
    title?: ReactNode | undefined;
    description?: ReactNode | undefined;
    isLoading?: boolean | undefined;
    variant?: ToastContentValue["variant"] | undefined;
};

// The toast viewport needs a full landmark tree that happy-dom cannot provide,
// so the dialog is checked against the notifications it raises. Spies (not
// module mocks) keep every other test in the run on the real implementation.
const created: CapturedToast[] = [];
const updates: CapturedToast[] = [];
let addSpy: ReturnType<typeof spyOn<typeof toastQueue, "add">> | undefined;
let updateSpy: ReturnType<typeof spyOn<typeof toast, "update">> | undefined;

beforeEach(() => {
    created.length = 0;
    updates.length = 0;
    addSpy = spyOn(toastQueue, "add").mockImplementation(((content: ToastContentValue) => {
        created.push(content);
        return "toast-id";
    }) as typeof toastQueue.add);
    updateSpy = spyOn(toast, "update").mockImplementation(((
        id: string,
        message: ReactNode,
        options?: HeroUIToastOptions,
    ) => {
        updates.push({ title: message, ...options });
        return id;
    }) as typeof toast.update);
});

afterEach(() => {
    addSpy?.mockRestore();
    updateSpy?.mockRestore();
    cleanup();
});

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

test("the upload closes the dialog at once and reports progress in a toast", async () => {
    const uploaded: File[] = [];
    const opened: boolean[] = [];
    let finish: ((value: PlanExcelUploadResult) => void) | undefined;
    let report: ((state: PlanImportJobState) => void) | undefined;
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={(open) => opened.push(open)}
            onUpload={(file, onProgress) => {
                uploaded.push(file);
                report = onProgress;
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

    // The dialog steps aside at once: the load continues in a toast, so the
    // grid behind it stays usable.
    expect(opened).toContain(false);
    expect(created).toHaveLength(1);
    const createdToast = created[0];
    if (!createdToast) throw new Error("the load did not raise a toast");
    expect(createdToast.title).toBe("Загрузка файла в базу");
    expect(createdToast.isLoading).toBe(true);
    // Until the server sizes the work, the toast bar only shows motion.
    const initial = render(<>{createdToast.description}</>);
    expect(initial.getByRole("progressbar").hasAttribute("aria-valuenow")).toBe(false);
    initial.unmount();
    expect(uploaded).toEqual([file]);

    // Write phase: 30 of 80 units (the preflight's 40 rows × write + recompute).
    await act(async () => {
        report?.({ progress: 30, total: 80, phase: "write" });
    });
    const running = updates.at(-1) as CapturedToast;
    expect(running.title).toBe("Загрузка файла в базу");
    expect(running.isLoading).toBe(true);
    const bar = render(<>{running.description}</>);
    const progress = bar.getByRole("progressbar");
    expect(progress.getAttribute("aria-valuenow")).toBe("30");
    expect(progress.getAttribute("aria-valuemax")).toBe("80");
    expect(bar.getByText("Запись строк...")).toBeTruthy();
    expect(bar.getByText("38%")).toBeTruthy();
    bar.unmount();

    await act(async () => {
        report?.({ progress: 60, total: 80, phase: "recompute" });
    });
    expect(updates.at(-1)?.title).toBe("Загрузка файла в базу");
    const recompute = render(<>{updates.at(-1)?.description}</>);
    expect(recompute.getByText("Пересчёт формул...")).toBeTruthy();
    expect(recompute.getByText("75%")).toBeTruthy();
    recompute.unmount();

    await act(async () => {
        finish?.({ status: "done", newRows: 10, updatedRows: 30 });
    });

    // The running toast settles in place into the summary.
    expect(updates.at(-1)).toMatchObject({
        title: "Файл загружен",
        description: "Новых строк: 10 · Обновлено строк: 30",
        isLoading: false,
        variant: "success",
    });
});

test("a failed load settles the toast as an error instead of blocking the dialog", async () => {
    const opened: boolean[] = [];
    const view = render(
        <PlanExcelImportModal
            isOpen
            onFileSelected={() => {}}
            onOpenChange={(open) => opened.push(open)}
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

    expect(opened).toContain(false);
    expect(updates.at(-1)).toMatchObject({
        title: "Не удалось загрузить файл",
        description: "Строка 5: значение «ОКР» не найдено в справочнике.",
        isLoading: false,
        variant: "danger",
    });
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
