import { describe, expect, test } from "bun:test";

import { groupSkippedCells } from "@/shared/grid/skipped-cell-reasons";

type Entry = readonly [string, string];

/** The original inline transformation, kept as an equivalence oracle. */
function legacyDescription(entries: Iterable<Entry>): string {
    const reasons = new Map<string, string[]>();
    for (const [title, reason] of entries) {
        const titles = reasons.get(reason) ?? [];
        titles.push(title);
        reasons.set(reason, titles);
    }
    return [...reasons.entries()]
        .map(([reason, titles]) => `${titles.map((title) => `«${title}»`).join(", ")}: ${reason}`)
        .join("; ");
}

describe("groupSkippedCells", () => {
    test("empty input yields an empty string", () => {
        expect(groupSkippedCells([])).toBe("");
        expect(groupSkippedCells(new Map())).toBe("");
    });

    test("one reason with many labels joins them with commas", () => {
        const entries: Entry[] = [
            ["Потребность / План, т", "расчётное поле"],
            ["Объём, т", "расчётное поле"],
            ["Отклонение %", "расчётное поле"],
        ];
        expect(groupSkippedCells(entries)).toBe(
            "«Потребность / План, т», «Объём, т», «Отклонение %»: расчётное поле",
        );
    });

    test("many reasons join with semicolons in first-seen order", () => {
        const entries: Entry[] = [
            ["Сырьевая позиция", "фиксированное поле"],
            ["Потребность / План, т", "расчётное поле"],
            ["Базис поставки", "поле недоступно для редактирования"],
        ];
        expect(groupSkippedCells(entries)).toBe(
            "«Сырьевая позиция»: фиксированное поле; «Потребность / План, т»: расчётное поле; «Базис поставки»: поле недоступно для редактирования",
        );
    });

    test("a duplicate label under a different reason appears in both groups", () => {
        const entries: Entry[] = [
            ["Актив", "ячейка занята другим пользователем"],
            ["Актив", "строка недоступна"],
            ["Актив", "ячейка занята другим пользователем"],
        ];
        expect(groupSkippedCells(entries)).toBe(
            "«Актив», «Актив»: ячейка занята другим пользователем; «Актив»: строка недоступна",
        );
    });

    test("interleaved reasons keep label and reason insertion order", () => {
        const entries: Entry[] = [
            ["A", "r1"],
            ["B", "r2"],
            ["C", "r1"],
            ["D", "r3"],
            ["E", "r2"],
        ];
        expect(groupSkippedCells(entries)).toBe("«A», «C»: r1; «B», «E»: r2; «D»: r3");
    });

    test("Russian text is reproduced exactly", () => {
        const entries: Entry[] = [
            ["Сырьевая позиция", "фиксированное поле"],
            ["Потребность / План, т", "расчётное поле"],
            ["Базис поставки", "ячейка занята другим пользователем"],
            ["Сотрудник актива", "строка недоступна"],
        ];
        expect(groupSkippedCells(entries)).toBe(
            "«Сырьевая позиция»: фиксированное поле; «Потребность / План, т»: расчётное поле; «Базис поставки»: ячейка занята другим пользователем; «Сотрудник актива»: строка недоступна",
        );
    });

    test("unusual label and reason characters are quoted literally, not escaped", () => {
        const label =
            '«Внутренние «кавычки»»; запятая, двоеточие: и \n перенос\u00a0NBSP 🚀 "двойные"';
        const reason = "причина; с ; двоеточием: и «кавычками»";
        expect(groupSkippedCells([[label, reason]])).toBe(`«${label}»: ${reason}`);
    });

    test("accepts any iterable and does not mutate the input map", () => {
        const input = new Map<string, string>([
            ["A", "r"],
            ["B", "r"],
        ]);
        expect(groupSkippedCells(input)).toBe("«A», «B»: r");
        expect([...input]).toEqual([
            ["A", "r"],
            ["B", "r"],
        ]);

        function* generated(): IterableIterator<Entry> {
            yield ["X", "r1"];
            yield ["Y", "r2"];
        }
        expect(groupSkippedCells(generated())).toBe("«X»: r1; «Y»: r2");
    });

    test("matches the original inline block on representative inputs", () => {
        const fixtures: Entry[][] = [
            [],
            [["Сырьевая позиция", "фиксированное поле"]],
            [
                ["Потребность / План, т", "расчётное поле"],
                ["Объём, т", "расчётное поле"],
                ["Базис поставки", "поле недоступно для редактирования"],
                ["Потребность / План, т", "расчётное поле"],
            ],
            [
                ["Актив", "ячейка занята другим пользователем"],
                ["Актив", "строка недоступна"],
                ["Актив", "ячейка занята другим пользователем"],
            ],
            [
                ["Потребность / План, т", "расчётное поле"],
                ["Базис поставки", "STRANGE\n«″» ;,: 🚀"],
            ],
        ];
        for (const entries of fixtures) {
            expect(groupSkippedCells(entries)).toBe(legacyDescription(entries));
        }
    });
});
