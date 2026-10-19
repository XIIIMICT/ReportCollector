import { afterEach, expect, test } from "bun:test";

import { cleanup, render, screen } from "@testing-library/react";

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
