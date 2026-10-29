import { afterEach, expect, test } from "bun:test";

import { AlertDialog, Button, Modal } from "@heroui/react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import { ControlledAlertDialog } from "../controlled-alert-dialog";
import { hasOpenModal } from "../modal-presence";

afterEach(cleanup);

test("no modal is reported while nothing is open", () => {
    expect(hasOpenModal()).toBe(false);
    render(<Button>Открыть</Button>);
    expect(hasOpenModal()).toBe(false);
});

test("an open alert dialog owns Escape", async () => {
    // Simulate the grid's window-capture shortcut: it must stand down while a
    // dialog is open, or the Escape that closes the dialog would also clear the
    // grid selection and the dialog would read «Удалить записи (0)».
    let selectionCleared = 0;
    const gridEscape = (event: KeyboardEvent) => {
        if (event.key === "Escape" && !hasOpenModal()) selectionCleared += 1;
    };
    window.addEventListener("keydown", gridEscape, true);
    render(
        <ControlledAlertDialog isOpen onOpenChange={() => {}}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Heading>Удалить записи?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>Записи будут удалены.</AlertDialog.Body>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>,
    );
    try {
        await waitFor(() => expect(hasOpenModal()).toBe(true));
        fireEvent.keyDown(document.body, { key: "Escape" });
        expect(selectionCleared).toBe(0);
    } finally {
        window.removeEventListener("keydown", gridEscape, true);
    }
});

test("a plain modal also counts as an overlay", async () => {
    render(
        <Modal.Backdrop isOpen onOpenChange={() => {}}>
            <Modal.Container>
                <Modal.Dialog>
                    <Modal.Header>
                        <Modal.Heading>Профиль</Modal.Heading>
                    </Modal.Header>
                    <Modal.Body>…</Modal.Body>
                </Modal.Dialog>
            </Modal.Container>
        </Modal.Backdrop>,
    );
    await waitFor(() => expect(hasOpenModal()).toBe(true));
});
