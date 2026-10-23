"use client";

import { AlertDialog, Button, ButtonGroup, Spinner } from "@heroui/react";
import { Substract, Xmark } from "iconoir-react";
import { useEffect, useState } from "react";

import { SmartSelect } from "@/shared/ui/smart-select";

export type ReplaceTargetOption = {
    id: string;
    name: string;
};

const EMPTY_TARGET = "__empty__";

/**
 * «Заменить» dictionary value dialog: re-points every reference to another
 * option (or clears it) and then deletes the replaced value.
 *
 * The replaced option is never offered as a target; «Пустое значение» reports
 * `null`, which the server reads as "delete the value instead of replacing".
 */
export function ReplaceRecordDialog({
    isOpen,
    isPending,
    label,
    options,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    label: string;
    /** Candidates to replace with (the replaced one must not be included). */
    options: ReplaceTargetOption[];
    onOpenChange: (open: boolean) => void;
    /** `null` — «Пустое значение». */
    onConfirm: (targetId: string | null) => void;
}): React.ReactNode {
    const [target, setTarget] = useState<string>(EMPTY_TARGET);

    // Every open starts from «Пустое значение».
    useEffect(() => {
        if (isOpen) setTarget(EMPTY_TARGET);
    }, [isOpen]);

    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="warning">
                                <Substract />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Заменить значение?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            <p>
                                Выберите значение справочника, на которое хотите заменить «{label}».
                                Операция выполнится, если это не нарушит ограничений уникальности в
                                связанных таблицах.
                            </p>
                            <SmartSelect
                                ariaLabel="Значение для замены"
                                onChange={(value) =>
                                    setTarget(typeof value === "string" ? value : EMPTY_TARGET)
                                }
                                options={options.map((option) => ({
                                    id: option.id,
                                    name: option.name,
                                }))}
                                nullOption={{ label: "Пустое значение", present: true }}
                                value={target}
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
                                    Отмена
                                </Button>
                                <Button
                                    variant="danger"
                                    onPress={() =>
                                        onConfirm(target === EMPTY_TARGET ? null : target)
                                    }
                                    isDisabled={isPending}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? (
                                        <Spinner color="current" size="sm" />
                                    ) : (
                                        <Substract aria-hidden="true" />
                                    )}
                                    Заменить
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}
