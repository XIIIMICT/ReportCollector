"use client";

import { FieldError, NumberField, Popover } from "@heroui/react";
import { useState } from "react";

export type RowRange = { from: number | null; to: number | null };

/**
 * The «№» (row-number) range filter — the same anchored popover as the column
 * filters, but with two `NumberField`s («От»/«До») instead of the value-list
 * Autocomplete (and no increment/decrement buttons). An empty bound means
 * «from the first» / «to the last»; «От» may not exceed «До» (the fields turn
 * invalid and the range is not applied until fixed). Applies when it closes.
 */
export function RowRangeFilter({
    anchor,
    initial,
    onApply,
    onClose,
}: {
    anchor: DOMRect;
    initial: RowRange | null;
    onApply: (next: RowRange | null) => void;
    onClose: () => void;
}) {
    const [from, setFrom] = useState<number | undefined>(initial?.from ?? undefined);
    const [to, setTo] = useState<number | undefined>(initial?.to ?? undefined);
    const invalid = from !== undefined && to !== undefined && from > to;

    const close = () => {
        if (!invalid) {
            onApply(
                from === undefined && to === undefined
                    ? null
                    : { from: from ?? null, to: to ?? null },
            );
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
            <Popover.Content className="row-range-filter__popover">
                <Popover.Dialog>
                    <div className="row-range-filter">
                        <div className="row-range-filter__fields">
                            <NumberField
                                {...(from !== undefined && { value: from })}
                                onChange={setFrom}
                                minValue={1}
                                isInvalid={invalid}
                                variant="secondary"
                                aria-label="От"
                            >
                                <NumberField.Group>
                                    <NumberField.Input placeholder="От" />
                                </NumberField.Group>
                            </NumberField>
                            <span aria-hidden="true">—</span>
                            <NumberField
                                {...(to !== undefined && { value: to })}
                                onChange={setTo}
                                minValue={1}
                                isInvalid={invalid}
                                variant="secondary"
                                aria-label="До"
                            >
                                <NumberField.Group>
                                    <NumberField.Input placeholder="До" />
                                </NumberField.Group>
                                <FieldError>«От» не может быть больше «До».</FieldError>
                            </NumberField>
                        </div>
                    </div>
                </Popover.Dialog>
            </Popover.Content>
        </Popover>
    );
}
