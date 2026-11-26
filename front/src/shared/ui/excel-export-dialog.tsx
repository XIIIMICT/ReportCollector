"use client";

import { Button, Description, Modal, Switch } from "@heroui/react";
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
                        <div className="flex flex-col gap-1">
                            <Switch isSelected={useFilters} onChange={setUseFilters}>
                                <Switch.Content>
                                    <Switch.Control>
                                        <Switch.Thumb />
                                    </Switch.Control>
                                    Учитывать фильтры и сортировки
                                </Switch.Content>
                            </Switch>
                            <Description>
                                Включено — «видимая таблица»; выключено — выгрузка всей таблицы без
                                текущего вида.
                            </Description>
                        </div>
                        <div className="flex flex-col gap-1">
                            <Switch isSelected={valuesOnly} onChange={setValuesOnly}>
                                <Switch.Content>
                                    <Switch.Control>
                                        <Switch.Thumb />
                                    </Switch.Control>
                                    Сохранять значения вместо формул
                                </Switch.Content>
                            </Switch>
                            <Description>
                                Включено — посчитанные один раз значения (файл меньше и открывается
                                быстрее).
                            </Description>
                        </div>
                    </Modal.Body>
                    <Modal.Footer>
                        <Button variant="tertiary" onPress={() => onOpenChange(false)}>
                            <Xmark aria-hidden="true" /> Отменить
                        </Button>
                        <Button
                            onPress={() => {
                                onOpenChange(false);
                                onConfirm({ useFilters, valuesOnly });
                            }}
                        >
                            <Download aria-hidden="true" /> Скачать
                        </Button>
                    </Modal.Footer>
                </Modal.Dialog>
            </Modal.Container>
        </Modal.Backdrop>
    );
}
