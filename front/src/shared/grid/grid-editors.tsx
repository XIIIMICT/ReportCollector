import {
    Autocomplete,
    Button,
    EmptyState,
    ListBox,
    SearchField,
    useFilter,
    type Key,
} from "@heroui/react";
import type { EditorType } from "@revolist/react-datagrid";
import { useMemo, useRef, useState } from "react";

const EMPTY_REFERENCE = "__empty_reference__";

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

function normalizeStringArray(value: unknown): string[] {
    if (!Array.isArray(value)) return [];
    return value
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim());
}
