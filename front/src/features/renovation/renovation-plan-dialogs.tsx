/**
 * Confirmation dialogs of the renovation plan lifecycle.
 *
 * Split out of `renovation-workspace.tsx`: they are self-contained AlertDialogs
 * with no state of their own — the workspace owns every open flag and handler.
 */

import { AlertDialog, Button, ButtonGroup, Spinner } from "@heroui/react";
import { CheckCircle, Plus, Send, WarningTriangle, Xmark } from "iconoir-react";

import { ControlledAlertDialog } from "@/shared/ui/controlled-alert-dialog";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";

export function AddRowDialog({
    isOpen,
    isPending,
    assets,
    selectedKey,
    onSelectionChange,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    assets: Array<{ id: string; name: string }>;
    selectedKey: string | null;
    onSelectionChange: (key: string | null) => void;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="success">
                                <Plus />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Добавить строку плана</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            <div className="add-row-dialog__field">
                                <SearchAutocomplete
                                    placeholder="Предприятие"
                                    compactLabel="Предприятие"
                                    items={assets}
                                    selectedKey={selectedKey}
                                    onSelectionChange={onSelectionChange}
                                />
                                <p className="add-row-dialog__hint">
                                    Пустая строка для выбранного предприятия будет добавлена в конец
                                    плана.
                                </p>
                            </div>
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
                                    variant="primary"
                                    onPress={onConfirm}
                                    isDisabled={isPending || selectedKey === null}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? <Spinner color="current" size="sm" /> : <Plus />}
                                    Добавить
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}

export function SubmitPlanDialog({
    isOpen,
    isPending,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="warning">
                                <Send />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                Отправить план на согласование?
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            Текущая версия плана будет зафиксирована и передана на согласование.
                            Правки в неё будут недоступны до завершения согласования.
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
                                    variant="primary"
                                    onPress={onConfirm}
                                    isDisabled={isPending}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? <Spinner color="current" size="sm" /> : <Send />}
                                    Отправить
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}

export function SubmitBlockedDialog({
    isOpen,
    message,
    onOpenChange,
}: {
    isOpen: boolean;
    message: string;
    onOpenChange: (open: boolean) => void;
}) {
    return (
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="danger">
                                <WarningTriangle />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                План нельзя отправить на согласование
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>{message}</AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button variant="primary" onPress={() => onOpenChange(false)}>
                                Понятно
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}

export function ApprovePlanDialog({
    isOpen,
    isPending,
    pendingCreatedBy,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    pendingCreatedBy: string | null;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="success">
                                <CheckCircle />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Согласовать план?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            {pendingCreatedBy
                                ? `Версия, отправленная ${pendingCreatedBy}, будет утверждена и заархивирована, после чего откроется новый рабочий план.`
                                : "Версия будет утверждена и заархивирована, после чего откроется новый рабочий план."}
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
                                    variant="primary"
                                    onPress={onConfirm}
                                    isDisabled={isPending}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? (
                                        <Spinner color="current" size="sm" />
                                    ) : (
                                        <CheckCircle />
                                    )}
                                    Согласовать
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}
