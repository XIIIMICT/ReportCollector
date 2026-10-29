import { AlertDialog, Button } from "@heroui/react";
import type { ComponentProps, ReactNode } from "react";

type ControlledAlertDialogProps = Omit<ComponentProps<typeof AlertDialog>, "children"> & {
    children: ReactNode;
};

/**
 * HeroUI's AlertDialog is built on React Aria's DialogTrigger. The trigger
 * responder must contain a pressable child even when the dialog is controlled
 * by an action outside this component. Keep a non-focusable, visually hidden
 * pressable anchor in the root so the controlled dialog follows that contract
 * without adding a second visible action or stealing focus.
 */
export function ControlledAlertDialog({ children, ...props }: ControlledAlertDialogProps) {
    return (
        <AlertDialog {...props}>
            <AlertDialog.Trigger aria-hidden="true" tabIndex={-1} className="sr-only">
                <Button aria-label="Открыть диалог" isIconOnly />
            </AlertDialog.Trigger>
            {children}
        </AlertDialog>
    );
}
