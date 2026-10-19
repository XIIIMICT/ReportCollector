import { afterEach, expect, test } from "bun:test";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { FormStatus } from "../form-status";

afterEach(cleanup);

const stats = null;

function room(status: "connecting" | "offline" | "online") {
    return {
        status,
        members: [],
        me: null,
    } as unknown as Parameters<typeof FormStatus>[0]["room"];
}

test("collaboration offline: the state stands alone, bold, without «На форме»", () => {
    render(<FormStatus room={room("offline")} selectionLabel={null} gridStats={stats} />);
    // No prefix…
    expect(screen.queryByText(/На форме/)).toBeNull();
    // …the message itself, and it is an emphasised <b>.
    const bold = screen.getByText(/Совместное редактирование недоступно/);
    expect(bold.tagName).toBe("B");
    expect(bold.closest(".form-status__presence--bold")).toBeTruthy();
});

test("collaboration connecting: same treatment", () => {
    render(<FormStatus room={room("connecting")} selectionLabel={null} gridStats={stats} />);
    expect(screen.queryByText(/На форме/)).toBeNull();
    const bold = screen.getByText(/Подключение к совместному редактированию/);
    expect(bold.tagName).toBe("B");
});

test("collaboration online keeps the «На форме:» prefix", () => {
    render(<FormStatus room={room("online")} selectionLabel={null} gridStats={stats} />);
    expect(screen.getByText(/На форме:/)).toBeTruthy();
    expect(document.querySelector(".form-status__presence--bold")).toBeNull();
});

test("tooltip lists other people first and self last, with a separate selection section", async () => {
    const me = {
        userId: "self",
        name: "Евтушенко",
        email: "self@example.ru",
        sessionId: "self-session",
    };
    const other = {
        userId: "other",
        name: "Кузнецов",
        email: "other@example.ru",
        sessionId: "other-session",
    };
    render(
        <FormStatus
            room={{ ...room("online")!, me, members: [me, other] }}
            selectionLabel="столбец 3, строка 1"
            gridStats={null}
        />,
    );
    const trigger = screen.getByRole("button");
    fireEvent.keyDown(document.body, { key: "Tab", code: "Tab" });
    act(() => {
        trigger.focus();
    });
    fireEvent.mouseEnter(trigger);
    fireEvent.mouseMove(trigger);
    await waitFor(() =>
        expect(document.querySelector(".form-status-detail__presence-list")).toBeTruthy(),
    );
    const list = document.querySelector(".form-status-detail__presence-list")!;
    expect(list.tagName).toBe("UL");
    const items = [...list.querySelectorAll("li")];
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toContain("Кузнецов");
    expect(items[1]?.textContent).toContain("Евтушенко");
    expect(list.closest(".form-status-detail__presence")?.nextElementSibling?.className).toBe(
        "form-status-detail__row",
    );
});

test("tooltip shows self as a list item even without other participants", async () => {
    const me = {
        userId: "self",
        name: "Евтушенко",
        email: "self@example.ru",
        sessionId: "self-session",
    };
    render(
        <FormStatus
            room={{ ...room("online")!, me, members: [] }}
            selectionLabel={null}
            gridStats={null}
        />,
    );
    const trigger = screen.getByRole("button");
    fireEvent.keyDown(document.body, { key: "Tab", code: "Tab" });
    act(() => {
        trigger.focus();
    });
    fireEvent.mouseEnter(trigger);
    fireEvent.mouseMove(trigger);
    await waitFor(() =>
        expect(document.querySelectorAll(".form-status-detail__presence-list li")).toHaveLength(1),
    );
    expect(document.querySelector(".form-status-detail__presence-list li")?.textContent).toContain(
        "Евтушенко",
    );
});
