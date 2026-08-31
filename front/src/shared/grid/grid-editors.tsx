import {
    Autocomplete,
    Button,
    Calendar,
    DatePicker,
    EmptyState,
    ListBox,
    SearchField,
    useFilter,
    type Key,
} from "@heroui/react";
import { CalendarDate, parseDate } from "@internationalized/date";
import type { EditorType } from "@revolist/react-datagrid";
import { useMemo, useRef, useState } from "react";

const EMPTY_REFERENCE = "__empty_reference__";

type SelectOption = { value: string; label: string };

export function SelectEditor({ column, save, close }: EditorType) {
    const rootRef = useRef<HTMLDivElement>(null);
    const options = useMemo(
        () => (Array.isArray(column.column.source) ? (column.column.source as SelectOption[]) : []),
        [column.column.source],
    );
    const current = String(column.value ?? column.model[String(column.prop)] ?? "");
    const { contains } = useFilter({ sensitivity: "base" });

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
            <Autocomplete
                fullWidth
                defaultOpen
                aria-label={String(column.column.name)}
                placeholder="Выберите значение"
                selectionMode="single"
                value={current || null}
                variant="secondary"
                onChange={(key) => save(key === null ? "" : String(key))}
            >
                <Autocomplete.Trigger className="grid-autocomplete-editor__hidden-trigger">
                    <Autocomplete.Value />
                    <Autocomplete.Indicator />
                </Autocomplete.Trigger>
                <Autocomplete.Popover>
                    <Autocomplete.Filter filter={contains}>
                        <SearchField
                            autoFocus
                            name="select-search"
                            aria-label="Найти значение"
                            variant="secondary"
                        >
                            <SearchField.Group>
                                <SearchField.SearchIcon />
                                <SearchField.Input placeholder="Найти…" />
                                <SearchField.ClearButton />
                            </SearchField.Group>
                        </SearchField>
                        <ListBox
                            renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}
                        >
                            {options.map((option) => (
                                <ListBox.Item
                                    key={option.value}
                                    id={option.value}
                                    textValue={option.label}
                                >
                                    {option.label}
                                    <ListBox.ItemIndicator />
                                </ListBox.Item>
                            ))}
                        </ListBox>
                    </Autocomplete.Filter>
                </Autocomplete.Popover>
            </Autocomplete>
        </div>
    );
}

export function MultiAssetEditor({ column, save, close }: EditorType) {
    const rootRef = useRef<HTMLDivElement>(null);
    const options = useMemo(
        () => normalizeStringArray(column.column.source),
        [column.column.source],
    );
    const [selected, setSelected] = useState<Key[]>(() =>
        normalizeStringArray(column.value ?? column.model[String(column.prop)]),
    );
    const { contains } = useFilter({ sensitivity: "base" });

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
            <Autocomplete
                fullWidth
                defaultOpen
                aria-label="Активы"
                placeholder="Выберите активы"
                selectionMode="multiple"
                value={selected}
                variant="secondary"
                onChange={(keys) => setSelected((keys ?? []) as Key[])}
            >
                <Autocomplete.Trigger className="grid-autocomplete-editor__hidden-trigger">
                    <Autocomplete.Value />
                    <Autocomplete.Indicator />
                </Autocomplete.Trigger>
                <Autocomplete.Popover>
                    <Autocomplete.Filter filter={contains}>
                        <SearchField
                            autoFocus
                            name="asset-search"
                            aria-label="Найти актив"
                            variant="secondary"
                        >
                            <SearchField.Group>
                                <SearchField.SearchIcon />
                                <SearchField.Input placeholder="Найти актив…" />
                                <SearchField.ClearButton />
                            </SearchField.Group>
                        </SearchField>
                        <ListBox
                            renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}
                        >
                            {options.map((option) => (
                                <ListBox.Item key={option} id={option} textValue={option}>
                                    {option}
                                    <ListBox.ItemIndicator />
                                </ListBox.Item>
                            ))}
                        </ListBox>
                    </Autocomplete.Filter>
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
                </Autocomplete.Popover>
            </Autocomplete>
        </div>
    );
}

export function ReferenceEditor({ column, save, close }: EditorType) {
    const rootRef = useRef<HTMLDivElement>(null);
    const values = useMemo(
        () => normalizeStringArray(column.column.source),
        [column.column.source],
    );
    const allowsEmpty = values.includes("");
    const options = values.filter(Boolean);
    const current = String(column.value ?? column.model[String(column.prop)] ?? "");
    const { contains } = useFilter({ sensitivity: "base" });

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
            <Autocomplete
                fullWidth
                defaultOpen
                aria-label="Справочное значение"
                placeholder="Выберите значение"
                selectionMode="single"
                value={current || (allowsEmpty ? EMPTY_REFERENCE : null)}
                variant="secondary"
                onChange={(key) => save(key === EMPTY_REFERENCE || key === null ? "" : String(key))}
            >
                <Autocomplete.Trigger className="grid-autocomplete-editor__hidden-trigger">
                    <Autocomplete.Value />
                    {allowsEmpty ? <Autocomplete.ClearButton /> : null}
                    <Autocomplete.Indicator />
                </Autocomplete.Trigger>
                <Autocomplete.Popover>
                    <Autocomplete.Filter filter={contains}>
                        <SearchField
                            autoFocus
                            name="reference-search"
                            aria-label="Найти значение"
                            variant="secondary"
                        >
                            <SearchField.Group>
                                <SearchField.SearchIcon />
                                <SearchField.Input placeholder="Найти…" />
                                <SearchField.ClearButton />
                            </SearchField.Group>
                        </SearchField>
                        <ListBox
                            renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}
                        >
                            {allowsEmpty ? (
                                <ListBox.Item id={EMPTY_REFERENCE} textValue="Не задан">
                                    Не задан
                                    <ListBox.ItemIndicator />
                                </ListBox.Item>
                            ) : null}
                            {options.map((option) => (
                                <ListBox.Item key={option} id={option} textValue={option}>
                                    {option}
                                    <ListBox.ItemIndicator />
                                </ListBox.Item>
                            ))}
                        </ListBox>
                    </Autocomplete.Filter>
                </Autocomplete.Popover>
            </Autocomplete>
        </div>
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
