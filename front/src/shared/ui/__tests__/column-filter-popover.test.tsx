import { afterEach, expect, mock, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

mock.module("@/shared/api/renovation", () => ({
    getRenovationPlanFacets: async (column: string) =>
        column === "col_003"
            ? {
                  column,
                  // The two-bucket ID filter: no value list, no «Пустые».
                  values: [
                      { value: "Корректные", count: 10 },
                      { value: "Некорректные", count: 2 },
                  ],
                  total: 2,
                  hasNulls: false,
              }
            : {
                  column,
                  values: [
                      { value: "Насос", count: 3 },
                      { value: "Шкаф", count: 2 },
                  ],
                  total: 2,
                  hasNulls: true,
              },
}));

const { ColumnFilterPopover } = await import("../column-filter-popover");

afterEach(cleanup);

const anchor = { left: 0, top: 0, width: 100 } as DOMRect;

async function openFilter(column = "col_011", firstLabel = "Насос") {
    const view = render(
        <ColumnFilterPopover
            column={column}
            columnLabel={column}
            anchor={anchor}
            initial={null}
            onApply={() => {}}
            onClose={() => {}}
        />,
    );
    await waitFor(() => expect(screen.getByRole("option", { name: firstLabel })).toBeTruthy());
    return view;
}

function master() {
    return screen.getByRole("option", { name: /Выбрать все/ });
}

test("separator sits between «Пустые» and the values", async () => {
    await openFilter();
    const separator = document.querySelector(".column-filter__separator");
    expect(separator).toBeTruthy();
    const nullOption = screen.getByRole("option", { name: "Пустые" });
    const firstValue = screen.getByRole("option", { name: "Насос" });
    expect(
        nullOption.compareDocumentPosition(separator!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
        separator!.compareDocumentPosition(firstValue) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
});

test("unchecking the last option unchecks «Выбрать все»", async () => {
    await openFilter();
    expect(master().getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("option", { name: "Насос" }));
    fireEvent.click(screen.getByRole("option", { name: "Шкаф" }));
    fireEvent.click(screen.getByRole("option", { name: "Пустые" }));

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
});

test("unchecking «Выбрать все» clears every option, «Пустые» included", async () => {
    await openFilter();
    expect(master().getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "true",
    );

    fireEvent.click(master());

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "false",
    );
    expect(screen.getByRole("option", { name: "Насос" }).getAttribute("aria-selected")).toBe(
        "false",
    );
    expect(screen.getByRole("option", { name: "Шкаф" }).getAttribute("aria-selected")).toBe(
        "false",
    );
});

test("ID column: unchecking both buckets unchecks «Выбрать все»", async () => {
    await openFilter("col_003", "Корректные");
    expect(master().getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("option", { name: "Корректные" }));
    fireEvent.click(screen.getByRole("option", { name: "Некорректные" }));

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
});

test("ID column: checking both buckets back checks «Выбрать все»", async () => {
    await openFilter("col_003", "Корректные");
    const correct = () => screen.getByRole("option", { name: "Корректные" });
    const incorrect = () => screen.getByRole("option", { name: "Некорректные" });

    fireEvent.click(correct());
    fireEvent.click(incorrect());
    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));

    fireEvent.click(correct());
    fireEvent.click(incorrect());
    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("true"));
});
