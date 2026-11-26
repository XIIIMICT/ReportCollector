"use client";

import { Button, ButtonGroup, Modal, Switch } from "@heroui/react";
import { Download, Xmark } from "iconoir-react";
import { useState } from "react";

/** Options chosen in the Excel export dialog (shared by every project). */
export type ExcelExportOptions = {
    /** false = «вся таблица», true = «видимая таблица» (filters + sorting). */
    useFilters: boolean;
    /** true = write stored values, false = write live Excel formulas. */
    valuesOnly: boolean;
};

/**
 * Gate-free Excel export dialog used by all workspaces instead of the old
 * hover menu. Both toggles default to the safe/fast behavior: visible view and
 * values instead of the large, slow-to-open formula workbooks.
 */
export function ExcelExportDialog({
    isOpen,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
    onConfirm: (options: ExcelExportOptions) => void;
}) {
    const [useFilters, setUseFilters] = useState(true);
    const [valuesOnly, setValuesOnly] = useState(true);

    return (
        <Modal.Backdrop isOpen={isOpen} onOpenChange={onOpenChange}>
            <Modal.Container>
                <Modal.Dialog className="max-w-[420px]" aria-label="Выгрузка в Excel">
                    <Modal.CloseTrigger />
                    <Modal.Header>
                        <Modal.Heading>Выгрузка в Excel</Modal.Heading>
                    </Modal.Header>
                    <Modal.Body className="flex flex-col gap-5">
                        <Switch isSelected={useFilters} onChange={setUseFilters}>
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                {/* HeroUI's `.switch__content` is font-medium;
                                    a direct utility on the label keeps it regular. */}
                                <span className="font-normal">Учитывать фильтры и сортировки</span>
                            </Switch.Content>
                        </Switch>
                        <Switch isSelected={valuesOnly} onChange={setValuesOnly}>
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                <span className="font-normal">
                                    Сохранять значения вместо формул
                                </span>
                            </Switch.Content>
                        </Switch>
                    </Modal.Body>
                    <Modal.Footer>
                        <ButtonGroup>
                            <Button variant="tertiary" onPress={() => onOpenChange(false)}>
                                <Xmark aria-hidden="true" /> Отменить
                            </Button>
                            <Button
                                variant="primary"
                                onPress={() => {
                                    onOpenChange(false);
                                    onConfirm({ useFilters, valuesOnly });
                                }}
                            >
                                <ButtonGroup.Separator />
                                <Download aria-hidden="true" /> Скачать
                            </Button>
                        </ButtonGroup>
                    </Modal.Footer>
                </Modal.Dialog>
            </Modal.Container>
        </Modal.Backdrop>
    );
}
