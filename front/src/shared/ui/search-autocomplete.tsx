"use client";

import {
    Autocomplete,
    EmptyState,
    Input,
    Label,
    ListBox,
    SearchField,
    TextField,
    useFilter,
} from "@heroui/react";
import type { Key } from "react";
import { useEffect, useState } from "react";

import { useMediaQuery } from "@/shared/hooks/use-media-query";

type SearchAutocompleteItem = {
    description?: string;
    id: string;
    name: string;
};

type SearchAutocompleteProps = {
    items: SearchAutocompleteItem[];
    label?: string;
    placeholder: string;
    selectedKey: string | null;
    onSelectionChange: (key: string | null) => void;
    onOpenChange?: (open: boolean) => void;
    isDisabled?: boolean;
    /**
     * Short label shown in the trigger instead of the selected value on narrow
     * screens (below lg). The popover keeps its full width when open.
     */
    compactLabel?: string;
};

export function SearchAutocomplete({
    items,
    label,
    placeholder,
    selectedKey,
    onSelectionChange,
    onOpenChange,
    isDisabled = false,
    compactLabel,
}: SearchAutocompleteProps) {
    const isCompact = useMediaQuery("(width < 64rem)");
    const { contains } = useFilter({ sensitivity: "base" });
    const [isMounted, setIsMounted] = useState(false);

    useEffect(() => {
        setIsMounted(true);
    }, []);

    function handleSelectionChange(key: Key | null) {
        onSelectionChange(key ? String(key) : null);
    }

    if (!isMounted) {
        return (
            <TextField isDisabled name="autocomplete" aria-label={label ?? placeholder}>
                {label ? <Label>{label}</Label> : null}
                <Input placeholder={placeholder} value="" />
            </TextField>
        );
    }

    return (
        <Autocomplete
            fullWidth
            isDisabled={isDisabled}
            placeholder={placeholder}
            aria-label={label ?? placeholder}
            selectionMode="single"
            value={selectedKey}
            onChange={handleSelectionChange}
            onOpenChange={onOpenChange}
            variant="secondary"
        >
            {label ? <Label>{label}</Label> : null}
            <Autocomplete.Trigger>
                <Autocomplete.Value>
                    {(state) => {
                        if (isCompact && compactLabel) return compactLabel;
                        // Full trigger: the item name on the first line and its
                        // description («Доступна для редактирования» / fixer +
                        // date) below in a smaller muted font.
                        const selected = items.find((item) => item.id === selectedKey);
                        if (!selected) return state.selectedText;
                        return (
                            <span className="flex min-w-0 flex-col">
                                <span className="truncate">{selected.name}</span>
                                {selected.description ? (
                                    <span className="text-muted truncate text-xs">
                                        {selected.description}
                                    </span>
                                ) : null}
                            </span>
                        );
                    }}
                </Autocomplete.Value>
                <Autocomplete.Indicator />
            </Autocomplete.Trigger>
            <Autocomplete.Popover className="search-autocomplete-popover">
                <Autocomplete.Filter filter={contains}>
                    <SearchField
                        autoFocus
                        name="search"
                        aria-label={placeholder}
                        variant="secondary"
                    >
                        <SearchField.Group>
                            <SearchField.SearchIcon />
                            <SearchField.Input placeholder={placeholder} />
                        </SearchField.Group>
                    </SearchField>
                    <ListBox renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}>
                        {items.map((item) => (
                            <ListBox.Item
                                id={item.id}
                                key={item.id}
                                textValue={[item.name, item.description].filter(Boolean).join(" ")}
                            >
                                <div className="flex min-w-0 flex-col">
                                    <span className="truncate">{item.name}</span>
                                    {item.description ? (
                                        <span className="text-muted truncate text-xs">
                                            {item.description}
                                        </span>
                                    ) : null}
                                </div>
                                <ListBox.ItemIndicator />
                            </ListBox.Item>
                        ))}
                    </ListBox>
                </Autocomplete.Filter>
            </Autocomplete.Popover>
        </Autocomplete>
    );
}
