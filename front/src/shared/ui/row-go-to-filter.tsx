"use client";

import { NumberField, Popover } from "@heroui/react";
import { useRef, useState } from "react";

/**
 * «Перейти к строке» — the «№» corner popover. A single NumberField with the
 * row's sequential number in the CURRENT (filtered) scope, 1-based. The jump
 * happens when the popover closes (outside press, Escape or Enter); an empty
 * field closes without a jump. The number is clamped to [1, total].
 *
 * The value is read from the input on close as well as from `onChange`:
 * react-aria's NumberField only commits typed text on blur/Enter, so closing
 * the popover right after typing would otherwise lose the number.
 */
export function RowGoToFilter({
    anchor,
    total,
    onGoTo,
    onClose,
}: {
    anchor: DOMRect;
    /** Rows in the current filtered scope (the highest valid number). */
    total: number;
    onGoTo: (position: number) => void;
    onClose: () => void;
}) {
    const [value, setValue] = useState<number | undefined>(undefined);
    const inputRef = useRef<HTMLInputElement>(null);

    const close = () => {
        const raw = inputRef.current?.value?.trim() ?? "";
        const parsed = value ?? (raw === "" ? undefined : Number(raw.replace(",", ".")));
        if (parsed !== undefined && Number.isFinite(parsed)) {
            onGoTo(Math.max(1, Math.min(Math.round(parsed), Math.max(1, total))));
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
                </Popover.Dialog>
            </Popover.Content>
        </Popover>
    );
}
