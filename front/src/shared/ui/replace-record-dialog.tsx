"use client";

import { AlertDialog, Button, ButtonGroup, Spinner } from "@heroui/react";
import { DataTransferBoth, Xmark } from "iconoir-react";
import { useEffect, useState } from "react";

import { ControlledAlertDialog } from "@/shared/ui/controlled-alert-dialog";
import { SmartSelect } from "@/shared/ui/smart-select";

export type ReplaceTargetOption = {
    id: string;
    name: string;
    /** A muted second line inside the option and the trigger. */
    description?: string;
};

/**
 * Sentinel for «Пустое значение» — a real, selectable target that clears the
 * references instead of re-pointing them. It is a VALUE of this control, so it
 * has to be distinguishable from "nothing chosen yet" (in SmartSelect a `null`
 * selection renders as an untouched placeholder).
 */
const EMPTY_TARGET = "__empty__";

/**
 * «Заменить» dictionary value dialog: re-points every reference to another
 * option (or clears it) and then deletes the replaced value.
 *
 * The replaced option is never offered as a target. Nothing is selected when
 * the dialog opens — the confirm button stays disabled until the user picks
 * either another option or «Пустое значение» (which reports `null`; the server
 * reads that as "delete the value instead of replacing").
 */
export function ReplaceRecordDialog({
    isOpen,
    isPending,
    label,
    sectionLabel,
    options,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    label: string;
    /** The dictionary the value belongs to («Вид работ», «Предприятия»). */
    sectionLabel: string;
    /** Candidates to replace with (the replaced one must not be included). */
    options: ReplaceTargetOption[];
    onOpenChange: (open: boolean) => void;
    /** `null` — «Пустое значение». */
    onConfirm: (targetId: string | null) => void;
}): React.ReactNode {
    // `null` = nothing chosen yet; the sentinel = «Пустое значение».
    const [target, setTarget] = useState<string | null>(null);

    // Every open starts from "nothing chosen": the user must make a choice.
    useEffect(() => {
        if (isOpen) setTarget(null);
    }, [isOpen]);

    const hasChoice = target !== null;
    const confirm = () => {
        if (target === null) return;
        onConfirm(target === EMPTY_TARGET ? null : target);
    };

    return (
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable isKeyboardDismissDisabled={false}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="warning">
                                <DataTransferBoth aria-hidden="true" />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Заменить значение?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body className="replace-record-dialog__body">
                            <p className="replace-record-dialog__hint">
                                Выберите значение справочника «
                                <span className="dialog-accent">{sectionLabel}</span>
                                », на которое хотите заменить «
                                <span className="dialog-accent">{label}</span>». Операция
                                выполнится, если это не нарушит ограничений уникальности в связанных
                                таблицах.
                            </p>
                            {/* The select sits below the text with the dialog's own
                                spacing — `AlertDialog.Body` stacks its children in
                                a grid, so no manual margin is needed. */}
                            <SmartSelect
                                ariaLabel="Значение для замены"
                                placeholder="Выберите значение"
                                onChange={(value) =>
                                    setTarget(typeof value === "string" ? value : null)
                                }
                                options={[
                                    {
                                        id: EMPTY_TARGET,
                                        name: "Пустое значение",
                                        description: "Очистить ссылки на эту запись",
                                    },
                                    ...options.map((option) => ({
                                        id: option.id,
                                        name: option.name,
                                        ...(option.description
                                            ? { description: option.description }
                                            : {}),
                                    })),
                                ]}
                                value={target}
                                fullWidth
                            />
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
                                <Button
                                    variant="danger"
                                    onPress={confirm}
                                    isDisabled={isPending || !hasChoice}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? (
                                        <Spinner color="current" size="sm" />
                                    ) : (
                                        <DataTransferBoth aria-hidden="true" />
                                    )}
                                    Заменить запись
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}
