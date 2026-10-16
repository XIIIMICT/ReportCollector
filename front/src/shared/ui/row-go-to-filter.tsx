"use client";

import { Label, NumberField, Popover } from "@heroui/react";
import { useRef, useState } from "react";

/**
 * «Перейти к строке/столбцу» — the «№» corner popover.
 *
 * Two NumberFields: the row's SEQUENTIAL number in the current (filtered)
 * scope (1-based, clamped to [1, total]) and the column's FIXED strip number
 * (the workbook ordinal). The two differ on purpose — see the docs: rows
 * re-count in the filtered scope, columns keep their workbook number, so a
 * block filter that hides a column resolves the request to the nearest
 * VISIBLE one (the grid owns that fallback, `scrollToColumn`).
 *
 * The jump happens when the popover closes (outside press, Escape or Enter);
 * an empty field closes without a jump for that field.
 *
 * Values are read from the inputs on close as well as from `onChange`:
 * react-aria's NumberField only commits typed text on blur/Enter, so closing
 * the popover right after typing would otherwise lose the number.
 */
export function RowGoToFilter({
    anchor,
    total,
    maxColumn,
    onGoTo,
    onGoToColumn,
    onClose,
}: {
    anchor: DOMRect;
    /** Rows in the current filtered scope (the highest valid number). */
    total: number;
    /** Highest column number in the current scope (the strip's last number). */
    maxColumn: number;
    onGoTo: (position: number) => void;
    onGoToColumn: (number: number) => void;
    onClose: () => void;
}) {
    const [value, setValue] = useState<number | undefined>(undefined);
    const [column, setColumn] = useState<number | undefined>(undefined);
    const inputRef = useRef<HTMLInputElement>(null);
    const columnInputRef = useRef<HTMLInputElement>(null);

    const close = () => {
        const raw = inputRef.current?.value?.trim() ?? "";
        const parsed = value ?? (raw === "" ? undefined : Number(raw.replace(",", ".")));
        if (parsed !== undefined && Number.isFinite(parsed)) {
            onGoTo(Math.max(1, Math.min(Math.round(parsed), Math.max(1, total))));
        }
        const rawColumn = columnInputRef.current?.value?.trim() ?? "";
        const parsedColumn =
            column ?? (rawColumn === "" ? undefined : Number(rawColumn.replace(",", ".")));
        if (parsedColumn !== undefined && Number.isFinite(parsedColumn)) {
            // Columns are fixed, so a number above the last visible one is not
            // clamped but resolved by the grid to the nearest visible column.
            onGoToColumn(Math.max(1, Math.round(parsedColumn)));
        }
        onClose();
    };

    return (
        <Popover
            defaultOpen
            onOpenChange={(open) => {
                if (!open) close();
            }}
        >
            <Popover.Trigger
                className="column-filter__anchor"
                style={{ left: anchor.left, top: anchor.top, width: anchor.width }}
            >
                <span />
            </Popover.Trigger>
            <Popover.Content className="row-go-to-filter__popover">
                <Popover.Dialog>
                    <div className="row-go-to-filter">
                        <div className="row-go-to-filter__field">
                            <Label className="row-go-to-filter__label">Номер строки</Label>
                            <NumberField
                                {...(value !== undefined && { value })}
                                onChange={setValue}
                                minValue={1}
                                maxValue={Math.max(1, total)}
                                variant="secondary"
                                aria-label="Номер строки"
                            >
                                <NumberField.Group>
                                    <NumberField.Input
                                        ref={inputRef}
                                        placeholder={`1…${total}`}
                                        autoFocus
                                    />
                                </NumberField.Group>
                            </NumberField>
                        </div>
                        <div className="row-go-to-filter__field">
                            <Label className="row-go-to-filter__label">Номер столбца</Label>
                            <NumberField
                                {...(column !== undefined && { value: column })}
                                onChange={setColumn}
                                minValue={1}
                                variant="secondary"
                                aria-label="Номер столбца"
                            >
                                <NumberField.Group>
                                    <NumberField.Input
                                        ref={columnInputRef}
                                        placeholder={`1…${maxColumn}`}
                                    />
                                </NumberField.Group>
                            </NumberField>
                        </div>
                    </div>
                </Popover.Dialog>
            </Popover.Content>
        </Popover>
    );
}
