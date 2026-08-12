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

type SearchAutocompleteItem = {
    description?: string;
    id: string;
    name: string;
};

type SearchAutocompleteProps = {
    items: SearchAutocompleteItem[];
    label: string;
    placeholder: string;
    selectedKey: string | null;
    onSelectionChange: (key: string | null) => void;
    isDisabled?: boolean;
};

export function SearchAutocomplete({
    items,
    label,
    placeholder,
    selectedKey,
    onSelectionChange,
    isDisabled = false,
}: SearchAutocompleteProps) {
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
            <TextField isDisabled name={`autocomplete-${label}`}>
                <Label>{label}</Label>
                <Input placeholder={placeholder} value="" />
            </TextField>
        );
    }

    return (
        <Autocomplete
            fullWidth
            isDisabled={isDisabled}
            placeholder={placeholder}
            selectionMode="single"
            value={selectedKey}
            onChange={handleSelectionChange}
        >
            <Label>{label}</Label>
            <Autocomplete.Trigger>
                <Autocomplete.Value />
                <Autocomplete.Indicator />
            </Autocomplete.Trigger>
            <Autocomplete.Popover>
                <Autocomplete.Filter filter={contains}>
                    <SearchField autoFocus name="search" variant="secondary">
                        <SearchField.Group>
                            <SearchField.SearchIcon />
                            <SearchField.Input placeholder={placeholder} />
                        </SearchField.Group>
                    </SearchField>
                    <ListBox renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}>
                        {items.map((item) => (
                            <ListBox.Item id={item.id} key={item.id} textValue={item.name}>
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
