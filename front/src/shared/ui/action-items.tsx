/**
 * The action-bar items every workspace assembles by hand.
 *
 * Copy/paste, undo/redo and the Excel dropdown are the SAME buttons with the
 * same labels, icons, shortcuts and groups in all three projects; only the
 * handlers differ. They were pasted four times, and the copies already drifted
 * (one menu entry had an icon, the others did not).
 */

import { AlertDialog, Button, ButtonGroup, Spinner } from "@heroui/react";
import {
    Copy,
    DataTransferBoth,
    Download,
    PasteClipboard,
    Plus,
    Redo,
    Trash,
    Undo,
    Xmark,
} from "iconoir-react";
import { createElement, type ReactElement, type ReactNode } from "react";

import type { ActionItem } from "@/shared/ui/action-bar";
import { ControlledAlertDialog } from "@/shared/ui/controlled-alert-dialog";

/** The export scope both menu entries drive. */
export type ExportMode = "all" | "visible";

/**
 * «Заменить» activity of a dictionary section.
 *
 * Each project publishes it from its mounted dictionary tab and its footer
 * renders the same shared action: `show` says whether the section is a
 * replaceable dictionary at all, the rest mirrors the selection.
 */
export type DictionaryReplaceState = {
    show?: boolean;
    canReplace: boolean;
    blockedReason: string | null;
    onReplace: () => void;
};

/** What a workspace publishes to the action bar for the export button. */
export type ExportState = {
    pending: boolean;
    /**
     * Rows the CURRENT view can export (after filters, as the grid counts
     * them). An empty table has nothing to download, so the button is disabled
     * and says why — the rule is the same in every project.
     */
    rowCount?: number | undefined;
    onExport: (mode: ExportMode, valuesOnly?: boolean) => void;
    /** Present while a durable job runs: turns the button into «Отменить». */
    cancel?: (() => void) | undefined;
};

/** Keep row-count publications even when the download handler is unchanged. */
export function reconcileExportState(current: ExportState, next: ExportState): ExportState {
    return current.pending === next.pending &&
        current.rowCount === next.rowCount &&
        current.onExport === next.onExport &&
        current.cancel === next.cancel
        ? current
        : next;
}

/** The reason shown on an export button over an empty table. */
export const NOTHING_TO_EXPORT_REASON = "Нечего выгружать: таблица пуста.";

/**
 * «Заменить» for dictionary grids: re-points every reference to another
 * option and deletes the replaced one. Visible only where the section is a
 * reference dictionary and exactly one row is selected.
 */
export function buildReplaceAction({
    show = true,
    disabled,
    blockedReason,
    onReplace,
}: {
    show?: boolean;
    disabled: boolean;
    blockedReason: string | null;
    onReplace: () => void;
}): ActionItem[] {
    if (!show) return [];
    return [
        {
            id: "replace",
            label: "Заменить запись",
            icon: createElement(DataTransferBoth),
            variant: "danger",
            onPress: onReplace,
            disabled,
            ...(blockedReason && { disabledReason: blockedReason }),
            group: "row",
        },
    ];
}

/**
 * The Excel export button, shared by every project.
 *
 * Pressing it opens the export dialog (scope + value/formula choice); while a
 * durable job runs the same button becomes an enabled «Отменить» that cancels
 * the job on the server and stops local polling — the DocMasker pattern.
 */
export function buildExportAction({
    pending,
    disabled,
    rowCount,
    onPress,
    onCancel,
}: {
    pending: boolean;
    disabled: boolean;
    /** Rows the current view holds — 0 disables the export (nothing to save). */
    rowCount?: number | undefined;
    onPress: () => void;
    onCancel?: (() => void) | undefined;
}): ActionItem {
    const empty = rowCount === 0;
    return {
        id: "export",
        label: onCancel ? "Отменить" : "Скачать в Excel",
        icon: createElement(onCancel ? Xmark : Download),
        onPress: onCancel ?? onPress,
        disabled: disabled || (empty && onCancel === undefined),
        disabledReason:
            empty && !disabled && onCancel === undefined ? NOTHING_TO_EXPORT_REASON : null,
        pending,
        group: "download",
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
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable isKeyboardDismissDisabled={false}>
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
                            {count === 1 && label ? (
                                <p>
                                    Будет удалена запись «
                                    <span className="dialog-accent">{label}</span>
                                    ».
                                </p>
                            ) : null}
                            {count > 1 ? <p>Будут удалены записи в количестве {count}.</p> : null}
                            <p>
                                Операция выполняется только в том случае, если на удаляемые записи
                                ничто не ссылается. Иначе ни одна запись не будет удалена.
                            </p>
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <ButtonGroup>
                                <Button
                                    variant="tertiary"
                                    onPress={() => onOpenChange(false)}
                                    isDisabled={isPending}
                                >
                                    <Xmark aria-hidden="true" />
                                    Отменить
                                </Button>
                                <Button variant="danger" onPress={onConfirm} isDisabled={isPending}>
                                    <ButtonGroup.Separator />
                                    {isPending ? <Spinner color="current" size="sm" /> : <Trash />}
                                    Удалить
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
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
