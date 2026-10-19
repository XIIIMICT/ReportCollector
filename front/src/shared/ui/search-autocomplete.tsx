"use client";

import { SmartSelect, type SpecialOption } from "@/shared/ui/smart-select";

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
    /** Notifies the owner whether the popover is open (used to hold the
        fullscreen toolbar panels open while a dropdown is expanded). */
    onOpenChange?: (open: boolean) => void;
    isDisabled?: boolean;
    /**
     * Short label shown in the trigger instead of the selected value on narrow
     * screens (below lg). The popover keeps its full width when open.
     */
    compactLabel?: string;
    /**
     * Show `compactLabel` at every width (not only below lg). The call site
     * decides this per instance — the toolbar filters render compact because
     * "Блок"/"Архив" read better than a long selected value.
     */
    alwaysCompact?: boolean;
    /** The width below which the trigger shows `compactLabel` (see `SmartSelect`). */
    compactBelow?: "sm" | "lg";
    /**
     * The pinned «Все блоки» / «Все варианты» row (a non-value: picking it
     * reports no constraint). It is a property, not an entry of `items` — that
     * way it renders above the rule, like every other special row.
     */
    allOption?: SpecialOption;
    /** The value the `allOption` row maps onto (see `SmartSelect.allValue`). */
    allValue?: string;
    /** The pinned «Пустые» / «Не задан» row. */
    nullOption?: SpecialOption;
    /** An optional cap on the opened list's width, in `rem` (see `SmartSelect`). */
    maxPopoverWidth?: number;
};

/**
 * The toolbar filters' single-select dropdown.
 *
 * Now a thin adapter over the shared `SmartSelect` (which picks a `Select` for
 * a short list and a searchable `Autocomplete` for a long one, both opening on
 * hover) — kept as its own name because the toolbar call sites read better
 * with it and the `compactLabel` behaviour is toolbar-specific.
 */
export function SearchAutocomplete({
    items,
    label,
    placeholder,
    selectedKey,
    onSelectionChange,
    onOpenChange,
    isDisabled = false,
    compactLabel,
    alwaysCompact = false,
    allOption,
    allValue,
    nullOption,
    maxPopoverWidth,
    compactBelow,
}: SearchAutocompleteProps) {
    // The compact trigger label only applies below lg; SmartSelect handles that
    // through the same `compactLabel` prop (the value formatter respects it).
    return (
        <SmartSelect
            options={items}
            value={selectedKey}
            onChange={(value) => onSelectionChange(typeof value === "string" ? value : null)}
            {...(label !== undefined && { label })}
            placeholder={placeholder}
            isDisabled={isDisabled}
            {...(onOpenChange !== undefined && { onOpenChange })}
            {...(compactLabel !== undefined && { compactLabel })}
            {...(alwaysCompact && { alwaysCompact })}
            {...(allOption !== undefined && { allOption })}
            {...(allValue !== undefined && { allValue })}
            {...(nullOption !== undefined && { nullOption })}
            {...(maxPopoverWidth !== undefined && { maxPopoverWidth })}
            {...(compactBelow !== undefined && { compactBelow })}
        />
    );
}
