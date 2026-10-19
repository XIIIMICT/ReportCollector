/**
 * The action-bar items every workspace assembles by hand.
 *
 * Copy/paste, undo/redo and the Excel dropdown are the SAME buttons with the
 * same labels, icons, shortcuts and groups in all three projects; only the
 * handlers differ. They were pasted four times, and the copies already drifted
 * (one menu entry had an icon, the others did not).
 */

import { AlertDialog, Button, Spinner } from "@heroui/react";
import { Copy, Download, PasteClipboard, Plus, Redo, Trash, Undo } from "iconoir-react";
import { createElement, type ReactElement, type ReactNode } from "react";

import type { ActionItem } from "@/shared/ui/action-bar";

/** The export scope both menu entries drive. */
export type ExportMode = "all" | "visible";

/** What a workspace publishes to the action bar for the export button. */
export type ExportState = {
    pending: boolean;
    onExport: (mode: ExportMode) => void;
};

/**
 * The Excel export button with its two-entry dropdown.
 *
 * The button itself exports everything; the dropdown narrows to the visible
 * (filtered) table.
 */
export function buildExportAction({
    pending,
    disabled,
    onExport,
}: {
    pending: boolean;
    disabled: boolean;
    onExport: (mode: ExportMode) => void;
}): ActionItem {
    return {
        id: "export",
        label: "Скачать в Excel",
        icon: createElement(Download),
        onPress: () => onExport("all"),
        disabled,
        pending,
        group: "download",
        menu: [
            {
                id: "all",
                label: "Скачать всю таблицу",
                icon: createElement(Download, { "aria-hidden": "true" }),
                onPress: () => onExport("all"),
            },
            {
                id: "visible",
                label: "Скачать видимую таблицу",
                icon: createElement(Download, { "aria-hidden": "true" }),
                onPress: () => onExport("visible"),
            },
        ],
    };
}

/** Copy / paste for a grid whose clipboard is reachable. */
export function buildClipboardActions({
    show,
    showPaste = true,
    disabled,
    canPaste,
    onCopy,
    onPaste,
}: {
    show: boolean;
    /** The archive has no paste: the version is read-only. */
    showPaste?: boolean;
    disabled: boolean;
    canPaste: boolean;
    onCopy: () => void;
    onPaste: () => void;
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id: "copy",
            label: "Копировать",
            icon: createElement(Copy),
            onPress: onCopy,
            disabled,
            shortcut: { key: "C", aria: "Control+C" },
            group: "clipboard",
        },
        ...(showPaste
            ? [
                  {
                      id: "paste",
                      label: "Вставить",
                      icon: createElement(PasteClipboard),
                      onPress: onPaste,
                      disabled: disabled || !canPaste,
                      shortcut: { key: "V", aria: "Control+V" },
                      group: "clipboard",
                  } satisfies ActionItem,
              ]
            : []),
    ];
}
export function buildHistoryActions({
    show,
    offline,
    canUndo,
    canRedo,
    onUndo,
    onRedo,
}: {
    show: boolean;
    offline: boolean;
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id: "undo",
            label: "Отменить действие",
            icon: createElement(Undo),
            onPress: onUndo,
            disabled: offline || !canUndo,
            shortcut: { key: "Z", aria: "Control+Z" },
            group: "history",
        },
        {
            id: "redo",
            label: "Вернуть действие",
            icon: createElement(Redo),
            onPress: onRedo,
            disabled: offline || !canRedo,
            shortcut: { key: "Y", aria: "Control+Y" },
            group: "history",
        },
    ];
}

/** The one wording for "nothing selected" that every project reports. */
export const NO_ROW_SELECTED_REASON = "Выберите строку для удаления.";

/**
 * The delete button.
 *
 * It is NEVER hidden: a disabled button still renders and explains why on
 * hover (`disabledReason`). `blockedReason` is null exactly when the action is
 * possible; `disabled` is computed by the caller from its own rules.
 */
export function buildDeleteAction({
    show = true,
    label = "Удалить строку",
    disabled,
    blockedReason,
    onDelete,
    shortcut,
}: {
    show?: boolean;
    label?: string;
    disabled: boolean;
    blockedReason: string | null;
    onDelete: () => void;
    /** Only the «Сырьё» dictionaries advertise Ctrl+Delete. */
    shortcut?: ActionItem["shortcut"];
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id: "delete",
            label,
            icon: createElement(Trash),
            onPress: onDelete,
            variant: "danger",
            disabled,
            disabledReason: blockedReason,
            group: "row",
            ...(shortcut !== undefined && { shortcut }),
        },
    ];
}

/**
 * The shared confirmation step for every dictionary delete.
 *
 * It lived inside `raw-materials-workspace.tsx` and was imported from there by
 * both the renovation dictionary section and the КППК workspace — the `Trash`
 * icon it renders was left behind by a module that no longer imported it, so
 * the component threw `ReferenceError: Trash is not defined` on first render
 * (an error screen instead of the workspace). Living next to the other action
 * pieces both halves of the contract — the dialog and the button that opens it
 * — stay in one module, tested together.
 */
export function DeleteRecordDialog({
    isOpen,
    isPending,
    label,
    count,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    label: string | null;
    count: number;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}): ReactNode {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="danger">
                                <Trash />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                {count === 1 ? "Удалить запись?" : `Удалить записи (${count})?`}
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            {count === 1 && label ? `Будет удалена запись «${label}». ` : null}
                            {count > 1 ? `Будут удалены записи в количестве ${count}. ` : null}
                            Операция выполняется только в том случае, если на удаляемые записи ничто
                            не ссылается. Иначе ни одна запись не будет удалена.
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button
                                variant="tertiary"
                                onPress={() => onOpenChange(false)}
                                isDisabled={isPending}
                            >
                                Отмена
                            </Button>
                            <Button variant="danger" onPress={onConfirm} isDisabled={isPending}>
                                {isPending ? <Spinner color="current" size="sm" /> : <Trash />}
                                Удалить
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}

/** «Добавить строку» / «Добавить пользователя». */
export function buildAddRowAction({
    show,
    label,
    disabled,
    onAdd,
}: {
    show: boolean;
    label: string;
    disabled: boolean;
    onAdd: () => void;
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id: label === "Добавить пользователя" ? "create-user" : "add",
            label,
            icon: createElement(Plus),
            onPress: onAdd,
            disabled,
            group: "row",
        },
    ];
}

/** A primary lifecycle action: submit / approve / fix. */
export function buildPrimaryAction({
    show,
    id,
    label,
    icon,
    disabled,
    onPress,
}: {
    show: boolean;
    id: "submit" | "approve" | "fix";
    label: string;
    icon: ReactElement;
    disabled: boolean;
    onPress: () => void;
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id,
            label,
            icon,
            onPress,
            variant: "primary",
            disabled,
        } satisfies ActionItem,
    ];
}
