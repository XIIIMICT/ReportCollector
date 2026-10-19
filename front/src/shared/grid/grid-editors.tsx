import { Button, Calendar, DatePicker } from "@heroui/react";
import { CalendarDate, parseDate } from "@internationalized/date";
import type { EditorType } from "@revolist/react-datagrid";
import { useMemo, useRef, useState } from "react";

import { SmartSelect } from "@/shared/ui/smart-select";

type SelectOption = {
    value: string;
    label: string;
}; /** Wrap an in-cell editor: stops the grid's pointer/keyboard handling and closes on Escape. */
function EditorShell({ children, close }: { children: React.ReactNode; close: () => void }) {
    const rootRef = useRef<HTMLDivElement>(null);
    return (
        <div
            ref={rootRef}
            className="grid-autocomplete-editor"
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === "Escape") close();
            }}
        >
            {children}
        </div>
    );
}

export function SelectEditor({ column, save, close }: EditorType) {
    const options = useMemo(
        () => (Array.isArray(column.column.source) ? (column.column.source as SelectOption[]) : []),
        [column.column.source],
    );
    const current = String(column.value ?? column.model[String(column.prop)] ?? "");

    return (
        <EditorShell close={close}>
            <SmartSelect
                ariaLabel={String(column.column.name)}
                placeholder="Выберите значение"
                options={options.map((option) => ({ id: option.value, name: option.label }))}
                value={current || null}
                onChange={(value) => save(typeof value === "string" ? value : "")}
                embedded
                onDismiss={close}
            />
        </EditorShell>
    );
}

export function MultiAssetEditor({ column, save, close }: EditorType) {
    const options = useMemo(
        () => normalizeStringArray(column.column.source),
        [column.column.source],
    );
    const [selected, setSelected] = useState<string[]>(() =>
        normalizeStringArray(column.value ?? column.model[String(column.prop)]),
    );

    return (
        <EditorShell close={close}>
            <SmartSelect
                ariaLabel="Активы"
                placeholder="Выберите активы"
                options={options.map((option) => ({ id: option, name: option }))}
                value={selected}
                multiple
                onChange={(value) => setSelected(Array.isArray(value) ? value : [])}
                embedded
                onDismiss={close}
                popoverFooter={
                    <div className="grid-autocomplete-editor__actions">
                        <Button size="sm" variant="tertiary" onPress={() => close()}>
                            Отмена
                        </Button>
                        <Button
                            size="sm"
                            variant="primary"
                            onPress={() =>
                                save(options.filter((option) => selected.includes(option)))
                            }
                        >
                            Применить
                        </Button>
                    </div>
                }
            />
        </EditorShell>
    );
}

export function ReferenceEditor({ column, save, close }: EditorType) {
    const values = useMemo(
        () => normalizeStringArray(column.column.source),
        [column.column.source],
    );
    const allowsEmpty = values.includes("");
    const options = values.filter(Boolean);
    const current = String(column.value ?? column.model[String(column.prop)] ?? "");

    return (
        <EditorShell close={close}>
            <SmartSelect
                ariaLabel="Справочное значение"
                placeholder="Выберите значение"
                options={options.map((option) => ({ id: option, name: option }))}
                value={current || null}
                onChange={(value) => save(typeof value === "string" ? value : "")}
                embedded
                onDismiss={close}
                nullOption={{ label: "Не задан", present: allowsEmpty }}
            />
        </EditorShell>
    );
}

/**
 * Date editor for plain-date cells (value is an ISO `YYYY-MM-DD` string or
 * null/empty). Opens a HeroUI DatePicker on the same dblclick path as the
 * reference/select editors; picking (or clearing) commits the chosen value
 * by saving the ISO string and closing the editor.
 */
export function DateEditor({ column, save, close }: EditorType) {
    const rootRef = useRef<HTMLDivElement>(null);
    const [value, setValue] = useState<CalendarDate | null>(() =>
        parseDateCellValue(column.value ?? column.model[String(column.prop)]),
    );

    return (
        <div
            ref={rootRef}
            className="grid-autocomplete-editor"
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === "Escape") close();
            }}
        >
            <DatePicker
                aria-label={String(column.column.name)}
                value={value}
                onChange={(next) => {
                    setValue(next);
                    // The picker clears the value when the user deletes the
                    // field; a selected date serializes to `YYYY-MM-DD`.
                    save(next === null ? "" : next.toString());
                    close();
                }}
                granularity="day"
                shouldCloseOnSelect
                defaultOpen
                hideTimeZone
            >
                <DatePicker.Trigger className="grid-autocomplete-editor__hidden-trigger" />
                <DatePicker.Popover
                    className="grid-date-editor__popover"
                    // HeroUI's DatePicker.Popover does not wire a triggerRef to the
                    // react-aria Popover (unlike Autocomplete), so the calendar
                    // would float to the viewport's top-left. Anchor it explicitly
                    // to the editor wrapper (which fills the cell).
                    triggerRef={rootRef}
                >
                    <Calendar>
                        <Calendar.Header>
                            <Calendar.Heading />
                            <Calendar.NavButton slot="previous" aria-label="Предыдущий месяц">
                                ‹
                            </Calendar.NavButton>
                            <Calendar.NavButton slot="next" aria-label="Следующий месяц">
                                ›
                            </Calendar.NavButton>
                        </Calendar.Header>
                        <Calendar.Grid>
                            <Calendar.GridHeader>
                                {(day) => <Calendar.HeaderCell>{day}</Calendar.HeaderCell>}
                            </Calendar.GridHeader>
                            <Calendar.GridBody>
                                {(date) => <Calendar.Cell date={date} />}
                            </Calendar.GridBody>
                        </Calendar.Grid>
                    </Calendar>
                </DatePicker.Popover>
            </DatePicker>
        </div>
    );
}

/** Parse an ISO `YYYY-MM-DD` (or empty/null) cell value into a CalendarDate. */
function parseDateCellValue(value: unknown): CalendarDate | null {
    if (typeof value !== "string" || value.trim() === "") return null;
    try {
        return parseDate(value.trim().slice(0, 10));
    } catch {
        return null;
    }
}

function normalizeStringArray(value: unknown): string[] {
    if (!Array.isArray(value)) return [];
    return value
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim());
}
