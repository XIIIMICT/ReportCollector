"use client";

import { Button, Label, NumberField, Popover } from "@heroui/react";
import { Check, Xmark } from "iconoir-react";
import { useEffect, useRef, useState } from "react";

import { readStoredGoTo, writeStoredGoTo } from "@/shared/grid/use-grid-column-filters";
import { usePopoverDismiss } from "@/shared/hooks/use-popover-dismiss";
import { ExpandableButton } from "@/shared/ui/expandable-button";
import { ExpandableButtonGroup } from "@/shared/ui/expandable-button-group";

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
 * The footer commits with «Применить»/Enter; «Отменить», outside press and
 * Escape close without jumping. An empty field applies no jump for that field.
 *
 * Values are read from the inputs on close as well as from `onChange`:
 * react-aria's NumberField only commits typed text on blur/Enter, so closing
 * the popover right after typing would otherwise lose the number.
 */
/**
 * «Перейти к строке/столбцу» — the popover under a table's «№» corner.
 *
 * Shared by every table (the renovation plan and each project's grids): it
 * knows nothing about a project, only about two numbers and two callbacks, so
 * any grid that can count its rows and scroll can host it.
 *
 * Two NumberFields: the row's SEQUENTIAL number in the current (filtered)
 * scope (1-based, clamped to [1, total]) and the column's number (1-based; the
 * plan keeps it a FIXED strip number — the workbook ordinal — while a client
 * grid numbers its visible columns). The grid owns the resolution: a number
 * beyond the last visible column lands on the nearest one.
 *
 * The footer commits with «Применить»/Enter; «Отменить», outside press and
 * Escape close without jumping. An empty field applies no jump for that field.
 *
 * Values are read from the inputs on close as well as from `onChange`:
 * react-aria's NumberField only commits typed text on blur/Enter, so closing
 * the popover right after typing would otherwise lose the number.
 */
export function RowGoToFilter({
    anchor,
    total,
    maxColumn,
    rowLabel = "Номер строки",
    columnLabel = "Номер столбца",
    storageScope,
    onGoTo,
    onGoToColumn,
    onClose,
}: {
    anchor: DOMRect;
    /** Rows in the current filtered scope (the highest valid number). */
    total: number;
    /** Highest column number in the current scope. */
    maxColumn: number;
    /** Field captions — for tables whose rows are not a «строки» list. */
    rowLabel?: string;
    columnLabel?: string;
    /**
     * Where the two numbers are remembered (`localStorage`, per project +
     * section). Reopening the popover continues with the numbers used last time
     * instead of two empty fields; «Отменить» only cancels the jump, not what was
     * typed. Without a scope the fields start empty.
     */
    storageScope?: { project: string; section: string } | undefined;
    onGoTo: (position: number) => void;
    onGoToColumn: (number: number) => void;
    onClose: () => void;
}) {
    // The numbers last entered for this table seed the fields; an empty slot
    // stays an empty field (`NaN` is the NumberField's "no value").
    const stored = storageScope ? readStoredGoTo(storageScope.project, storageScope.section) : null;
    const [value, setValue] = useState(stored?.row ?? Number.NaN);
    const [column, setColumn] = useState(stored?.column ?? Number.NaN);
    const inputRef = useRef<HTMLInputElement>(null);
    const columnInputRef = useRef<HTMLInputElement>(null);
    const popoverRef = useRef<HTMLElement | null>(null);
    const { isOpen, dismiss } = usePopoverDismiss(popoverRef, onClose);
    const settled = useRef(false);

    // Focus the row field once the popover has been painted. Doing it with
    // `autoFocus` focused the input mid-render of the parent, which react-aria
    // performs as a `flushSync` call from a lifecycle method.
    //
    // The remembered numbers are written into the inputs HERE as well: the
    // NumberField keeps its state from a commit-on-blur, so a seeded value can
    // be displayed as `1` while the field never took it.
    useEffect(() => {
        const frame = window.requestAnimationFrame(() => {
            const input = inputRef.current;
            const columnField = columnInputRef.current;
            if (input && stored?.row != null) input.value = String(stored.row);
            if (columnField && stored?.column != null) columnField.value = String(stored.column);
            input?.focus();
        });
        return () => window.cancelAnimationFrame(frame);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /** Keep what the fields hold (an empty one clears its slot). */
    const remember = () => {
        if (!storageScope) return;
        const read = (input: HTMLInputElement | null): number | null => {
            const raw = input?.value?.trim() ?? "";
            if (raw === "") return null;
            const parsed = Number(raw.replace(",", "."));
            return Number.isFinite(parsed) ? Math.round(parsed) : null;
        };
        writeStoredGoTo(storageScope.project, storageScope.section, {
            row: read(inputRef.current),
            column: read(columnInputRef.current),
        });
    };

    const cancel = () => {
        // «Отменить» cancels the JUMP, not the typing: the numbers stay for the
        // next open, the same way the filter popover keeps its search draft.
        if (!settled.current) remember();
        settled.current = true;
        dismiss();
    };

    const apply = () => {
        if (settled.current) return;
        settled.current = true;
        const raw = inputRef.current?.value?.trim() ?? "";
        const parsed = raw === "" ? undefined : Number(raw.replace(",", "."));
        if (parsed !== undefined && Number.isFinite(parsed)) {
            onGoTo(Math.max(1, Math.min(Math.round(parsed), Math.max(1, total))));
        }
        const rawColumn = columnInputRef.current?.value?.trim() ?? "";
        const parsedColumn = rawColumn === "" ? undefined : Number(rawColumn.replace(",", "."));
        if (parsedColumn !== undefined && Number.isFinite(parsedColumn)) {
            // Columns are fixed, so a number above the last visible one is not
            // clamped but resolved by the grid to the nearest visible column.
            onGoToColumn(Math.max(1, Math.round(parsedColumn)));
        }
        remember();
        dismiss();
    };

    return (
        <Popover
            isOpen={isOpen}
            onOpenChange={(open) => {
                if (!open) cancel();
            }}
        >
            <Popover.Trigger
                className="column-filter__anchor"
                style={{ left: anchor.left, top: anchor.top, width: anchor.width }}
            >
                <Button aria-label="Открыть переход к строке" isIconOnly />
            </Popover.Trigger>
            <Popover.Content
                ref={popoverRef}
                isExiting={!isOpen}
                className="row-go-to-filter__popover"
            >
                <div
                    data-grid-keyboard-overlay
                    onKeyDownCapture={(event: React.KeyboardEvent<HTMLDivElement>) => {
                        if (event.key === "Escape") {
                            event.preventDefault();
                            event.stopPropagation();
                            cancel();
                        } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            event.stopPropagation();
                            apply();
                        }
                    }}
                >
                    <Popover.Dialog>
                        <div className="row-go-to-filter">
                            <div className="row-go-to-filter__field">
                                <Label className="row-go-to-filter__label">{rowLabel}</Label>
                                <NumberField
                                    value={value}
                                    onChange={setValue}
                                    formatOptions={{ useGrouping: false }}
                                    minValue={1}
                                    maxValue={Math.max(1, total)}
                                    variant="secondary"
                                    aria-label={rowLabel}
                                >
                                    <NumberField.Group>
                                        <NumberField.Input
                                            ref={inputRef}
                                            placeholder={`1…${total}`}
                                        />
                                    </NumberField.Group>
                                </NumberField>
                            </div>
                            <div className="row-go-to-filter__field">
                                <Label className="row-go-to-filter__label">{columnLabel}</Label>
                                <NumberField
                                    value={column}
                                    onChange={setColumn}
                                    formatOptions={{ useGrouping: false }}
                                    minValue={1}
                                    variant="secondary"
                                    aria-label={columnLabel}
                                >
                                    <NumberField.Group>
                                        <NumberField.Input
                                            ref={columnInputRef}
                                            placeholder={`1…${maxColumn}`}
                                        />
                                    </NumberField.Group>
                                </NumberField>
                            </div>
                            <ExpandableButtonGroup size="sm" className="row-go-to-filter__actions">
                                <ExpandableButton
                                    variant="tertiary"
                                    aria-label="Отменить"
                                    icon={<Xmark aria-hidden="true" />}
                                    onPress={cancel}
                                >
                                    Отменить
                                </ExpandableButton>
                                <ExpandableButton
                                    variant="primary"
                                    aria-label="Применить"
                                    icon={<Check aria-hidden="true" />}
                                    onPress={apply}
                                >
                                    Применить
                                </ExpandableButton>
                            </ExpandableButtonGroup>
                        </div>
                    </Popover.Dialog>
                </div>
            </Popover.Content>
        </Popover>
    );
}
