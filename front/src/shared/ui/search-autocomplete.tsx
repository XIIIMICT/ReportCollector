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
import { useEffect, useRef, useState } from "react";

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
    /** Notifies the owner whether the popover is open (used to hold the
        fullscreen toolbar panels open while a dropdown is expanded). */
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
    // Hover-open with hover-close. Closing is driven by POINT COORDINATES, not
    // by DOM enter/leave: HeroUI's open popover draws an overlay that sits above
    // the trigger, so a structural mouseleave fires on the trigger while the
    // pointer is motionless — that's the open/close flicker. Instead a document
    // mouseover checks whether the pointer is inside the trigger's or the
    // popover's bounding rect, and only then keeps it open.
    const [isOpen, setIsOpen] = useState(false);
    const openRef = useRef(false);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    const closeTimerRef = useRef<number | null>(null);

    useEffect(() => {
        openRef.current = isOpen;
        onOpenChange?.(isOpen);
    }, [isOpen, onOpenChange]);

    useEffect(() => {
        setIsMounted(true);
    }, []);

    useEffect(() => {
        return () => {
            if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
        };
    }, []);

    // While an autocomplete is mounted, track the pointer over the trigger or
    // its open popover by coordinates (robust against the overlay/portal), and
    // close ~200ms after it leaves both. The trigger region is the WHOLE wrapper
    // rect, not the inner button — the button is smaller than the clickable
    // component, so the pointer moving over the component's padding would
    // otherwise fall briefly outside and re-trigger a close (the in-component
    // flicker).
    useEffect(() => {
        function onMouseMove(event: MouseEvent) {
            if (!openRef.current) return;
            const wrapper = wrapperRef.current;
            const popover = document.querySelector<HTMLElement>(".search-autocomplete-popover");
            const x = event.clientX;
            const y = event.clientY;
            const wRect = wrapper?.getBoundingClientRect();
            const insideTrigger =
                !!wRect &&
                x >= wRect.left &&
                x <= wRect.right &&
                y >= wRect.top &&
                y <= wRect.bottom;
            const pRect = popover?.getBoundingClientRect();
            const insidePopover =
                !!pRect &&
                x >= pRect.left &&
                x <= pRect.right &&
                y >= pRect.top &&
                y <= pRect.bottom;
            if (insideTrigger || insidePopover) {
                if (closeTimerRef.current !== null) {
                    window.clearTimeout(closeTimerRef.current);
                    closeTimerRef.current = null;
                }
                return;
            }
            if (closeTimerRef.current === null) {
                closeTimerRef.current = window.setTimeout(() => {
                    closeTimerRef.current = null;
                    setIsOpen(false);
                }, 200);
            }
        }
        document.addEventListener("mousemove", onMouseMove, { passive: true });
        return () => {
            document.removeEventListener("mousemove", onMouseMove);
            if (closeTimerRef.current !== null) {
                window.clearTimeout(closeTimerRef.current);
                closeTimerRef.current = null;
            }
        };
    }, []);

    function openFromHover() {
        if (closeTimerRef.current !== null) {
            window.clearTimeout(closeTimerRef.current);
            closeTimerRef.current = null;
        }
        setIsOpen(true);
    }

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
        <div ref={wrapperRef} onMouseEnter={openFromHover}>
            <Autocomplete
                fullWidth
                isDisabled={isDisabled}
                placeholder={placeholder}
                aria-label={label ?? placeholder}
                selectionMode="single"
                value={selectedKey}
                onChange={handleSelectionChange}
                isOpen={isOpen}
                onOpenChange={setIsOpen}
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
                        <ListBox
                            renderEmptyState={() => <EmptyState>Ничего не найдено</EmptyState>}
                        >
                            {items.map((item) => (
                                <ListBox.Item
                                    id={item.id}
                                    key={item.id}
                                    textValue={[item.name, item.description]
                                        .filter(Boolean)
                                        .join(" ")}
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
        </div>
    );
}
