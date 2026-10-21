"use client";

import {
    Autocomplete,
    EmptyState,
    Label,
    ListBox,
    SearchField,
    Select,
    Separator,
    useFilter,
} from "@heroui/react";
import type { Key, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useMediaQuery } from "@/shared/hooks/use-media-query";

/**
 * One dropdown for the whole app: `Select` for a short list, `Autocomplete`
 * (with a search field) for a long one.
 *
 * The switch is by the number of options — `≤ threshold` (default 5) renders
 * the plain `Select`, more renders the searchable `Autocomplete`. Both are the
 * same underlying react-aria `Select`, so `multiple`, `value`, `onChange` and
 * the disabled state behave identically no matter which one wins.
 *
 * Every instance opens on HOVER (and on click/Enter/Space) and, by default,
 * closes ~200 ms after the pointer leaves BOTH trigger and popover. Closing is driven
 * by pointer coordinates, not by DOM `mouseleave`: an open HeroUI popover draws
 * an overlay above the trigger, so a structural leave fires while the pointer
 * is motionless — the classic open/close flicker (the same mechanism as
 * `SearchAutocomplete`).
 */

export type SmartSelectOption = {
    id: string;
    name: string;
    /** A muted second line inside the option and the trigger. */
    description?: string;
};

/** A pinned non-value row (see `nullOption` / `allOption`). */
export type SpecialOption = {
    /** The row's text («Пустые», «Все варианты»). */
    label: string;
    /** When false the row is not rendered at all. */
    present: boolean;
    /** An optional count shown as «label (N)». */
    count?: number;
};

export type SmartSelectProps = {
    /** The options; a custom `renderListbox` may omit them. */
    options?: SmartSelectOption[];
    /** A single selection — the id; a multi selection — the ids. */
    value: string | string[] | null;
    onChange: (value: string | string[] | null) => void;
    label?: string;
    placeholder?: string;
    /**
     * `multiple` is independent of the option count: a short multi list still
     * renders as a `Select` (it supports `selectionMode="multiple"`), a long
     * one as a searchable `Autocomplete`.
     */
    multiple?: boolean;
    /** The option count up to which `Select` is used (5 by default). */
    threshold?: number;
    isDisabled?: boolean;
    fullWidth?: boolean;
    variant?: "primary" | "secondary";
    /** Notifies the owner whether the popover is open (toolbar/panel holders). */
    onOpenChange?: (open: boolean) => void;
    /** Short label shown instead of the value on narrow screens. */
    compactLabel?: string;
    /**
     * Show `compactLabel` at EVERY width, not only below `lg`. The component
     * stays width-agnostic: whether the compact form is always on is the call
     * site's decision (this project renders most toolbar filters compact), so
     * the flag is explicit rather than implied by the breakpoint.
     */
    alwaysCompact?: boolean;
    /**
     * The width below which the trigger shows `compactLabel`. `lg` (64rem) is
     * the toolbar default; a toolbar carrying more controls (КППК
     * «Реализация» has four) narrows to the short labels only below `sm`.
     */
    compactBelow?: "sm" | "lg";
    /** Accessible name when there is no visible `label`. */
    ariaLabel?: string;
    /**
     * In-cell editor mode: the popover is open from the start, the trigger is
     * invisible (the cell itself is the anchor) and hover open/close is off.
     * `onDismiss` receives Escape — the grid closes its editor with it.
     */
    embedded?: boolean;
    onDismiss?: () => void;
    /**
     * The special "no value" row («Пустые», «Не задан») pinned ABOVE the regular
     * options, separated from them by a rule. Selecting it reports `null`.
     * `count` renders a `(N)` suffix (the filters' «Пустые (12)»).
     */
    nullOption?: SpecialOption;
    /**
     * The special "everything" row («Выбрать все», «Все варианты»), pinned first
     * and separated from the rest. In a multiple control it toggles the whole
     * selection; in a single one it reports the `allValue` sentinel so the owner
     * can translate it (a filter reads it as «no constraint»).
     */
    allOption?: SpecialOption;
    /** The value the `allOption` reports in a single-select control. */
    allValue?: string;
    /** Footer actions of the popover (e.g. «Отмена» / «Применить»). */
    popoverFooter?: ReactNode;
    /**
     * A replacement for the option list (and, for the searchable control, for
     * the search field too). Subclasses use it to render a custom list — the
     * column filter's «Выбрать все»/«Пустые» + paged values — while keeping
     * every hover/open/anchor mechanic of this component.
     */
    renderListbox?: (context: { searchable: boolean }) => ReactNode;
    /** Always render the searchable control regardless of the option count. */
    forceSearchable?: boolean;
    /** The trigger is an invisible anchor at (left, top) — the column filter. */
    anchor?: { left: number; top: number; width: number };
    /** Extra class on the popover. */
    popoverClassName?: string;
    /** Open the popover on mount (the column filter's anchor popover). */
    defaultOpen?: boolean;
    /** Close after the pointer leaves the trigger and popover (default: true). */
    closeOnUnhover?: boolean;
};

const DEFAULT_THRESHOLD = 5;
const HOVER_CLOSE_DELAY_MS = 200;
/**
 * Placement of an ANCHORED popover (the column filter: the anchor is the header
 * cell). `end` aligns the panel's RIGHT edge with the cell's right edge — the
 * header funnel sits at the header's end, so the pointer travels straight down
 * from the button into the filter. The default react-aria centering would push
 * the panel sideways whenever it is wider than the cell. RTL would mirror it.
 */
const ANCHOR_PLACEMENT = "bottom end" as const;
const ANCHOR_OFFSET = 2;
/** Sentinels for the pinned rows (never real option ids). */
const NULL_OPTION_ID = "__null_option__";
const ALL_OPTION_ID = "__all_option__";

/** Global capture shortcuts must leave Escape to an open select, portals included. */
export function hasOpenSmartSelect(): boolean {
    return document.querySelector('[data-smart-select-open="true"]') !== null;
}

export function SmartSelect({
    options = [],
    value,
    onChange,
    label,
    placeholder = "Выберите значение",
    multiple = false,
    threshold = DEFAULT_THRESHOLD,
    isDisabled = false,
    fullWidth = true,
    variant = "secondary",
    onOpenChange,
    compactLabel,
    alwaysCompact = false,
    compactBelow = "lg",
    ariaLabel,
    embedded = false,
    onDismiss,
    nullOption,
    allOption,
    allValue = "",
    popoverFooter,
    renderListbox,
    forceSearchable = false,
    anchor,
    popoverClassName,
    defaultOpen = false,
    closeOnUnhover = true,
}: SmartSelectProps) {
    const { contains } = useFilter({ sensitivity: "base" });
    // The toolbar shows a short label instead of the value on narrow screens.
    // `lg` (64rem) matches HeroUI's breakpoint scale; `sm` is 40rem.
    const isCompact = useMediaQuery(compactBelow === "sm" ? "(width < 40rem)" : "(width < 64rem)");
    // An embedded editor is open from the first paint (it IS the cell editor);
    // the column filter's anchor popover opens on mount too.
    const [isOpen, setIsOpen] = useState(embedded || defaultOpen);
    const openRef = useRef(false);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    const popoverRef = useRef<HTMLElement | null>(null);
    const closeTimerRef = useRef<number | null>(null);
    // Callers often pass an inline state adapter. Do not make the synchronization
    // effect depend on that function's identity: HeroUI may attach/detach item
    // refs during the resulting render, and the callback identity would turn the
    // harmless notification into a render loop.
    const onOpenChangeRef = useRef(onOpenChange);
    onOpenChangeRef.current = onOpenChange;

    useEffect(() => {
        openRef.current = isOpen;
        onOpenChangeRef.current?.(isOpen);
    }, [isOpen]);

    useEffect(() => {
        return () => {
            if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
        };
    }, []);

    // Pointer-coordinate tracking over the trigger and the open popover. An
    // embedded (in-cell) editor is exempt: it is open by construction and the
    // pointer strays across the grid all the time.
    useEffect(() => {
        if (embedded || !closeOnUnhover) return;
        function onMouseMove(event: MouseEvent) {
            if (!openRef.current) return;
            const x = event.clientX;
            const y = event.clientY;
            const inside = (rect: DOMRect | undefined) =>
                !!rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
            if (
                inside(wrapperRef.current?.getBoundingClientRect()) ||
                inside(popoverRef.current?.getBoundingClientRect())
            ) {
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
                }, HOVER_CLOSE_DELAY_MS);
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
    }, [closeOnUnhover, embedded]);

    // The popover is rendered in a body portal, outside the grid. Handle Escape
    // at document level so it closes this control without reaching the grid's
    // global Escape shortcut, which would otherwise clear the table selection.
    useEffect(() => {
        if (embedded) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Escape" || !openRef.current) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            setIsOpen(false);
        };
        document.addEventListener("keydown", onKeyDown, true);
        return () => document.removeEventListener("keydown", onKeyDown, true);
    }, [embedded]);

    function openFromHover() {
        if (isDisabled || embedded) return;
        if (closeTimerRef.current !== null) {
            window.clearTimeout(closeTimerRef.current);
            closeTimerRef.current = null;
        }
        setIsOpen(true);
    }

    const isMultiple = multiple;
    // The pinned rows map onto the stored value: «Все варианты» is the owner's
    // `allValue` sentinel, «Пустые»/«Не задан» is an empty value (no id).
    const selectedSingle =
        typeof value === "string" && value
            ? allValue !== "" && value === allValue
                ? ALL_OPTION_ID
                : value
            : null;
    const selectedMultiple = Array.isArray(value) ? value : [];

    const emitSingle = (key: Key | null) => {
        if (key === null || key === NULL_OPTION_ID) {
            onChange(null);
            return;
        }
        if (key === ALL_OPTION_ID) {
            onChange(allValue || null);
            return;
        }
        onChange(String(key));
    };
    const emitMultiple = (keys: Key[]) => {
        const next = keys.map(String);
        // The «everything» row stands for the whole list: when it appears in
        // the new selection, every real option is selected too (the pinned rows
        // are not values themselves). Removing it leaves the plain diff.
        if (allOption?.present) {
            const wasAll = selectedMultiple.includes(ALL_OPTION_ID);
            const isAll = next.includes(ALL_OPTION_ID);
            if (isAll && !wasAll) {
                onChange([
                    ...options.map((option) => option.id),
                    ...(next.includes(NULL_OPTION_ID) ? [NULL_OPTION_ID] : []),
                ]);
                return;
            }
        }
        onChange(next.filter((key) => key !== ALL_OPTION_ID));
    };

    // A long list gets the searchable control; a short one the plain Select.
    // `forceSearchable` lets a subclass (the column filter) keep the search
    // field whatever the currently loaded window holds.
    const useAutocomplete = forceSearchable || options.length > threshold;
    const specialLabel = (option: SpecialOption) =>
        option.count !== undefined ? `${option.label} (${option.count})` : option.label;

    const renderedOptions = useMemo<ReactNode[]>(() => {
        const special = [
            ...(allOption?.present
                ? [
                      <ListBox.Item
                          key={ALL_OPTION_ID}
                          id={ALL_OPTION_ID}
                          textValue={specialLabel(allOption)}
                      >
                          {specialLabel(allOption)}
                          <ListBox.ItemIndicator />
                      </ListBox.Item>,
                  ]
                : []),
            ...(nullOption?.present
                ? [
                      <ListBox.Item
                          key={NULL_OPTION_ID}
                          id={NULL_OPTION_ID}
                          textValue={specialLabel(nullOption)}
                      >
                          {specialLabel(nullOption)}
                          <ListBox.ItemIndicator />
                      </ListBox.Item>,
                  ]
                : []),
        ];
        return [
            ...special,
            // A rule between the pinned rows and the real values — only when
            // there is something on both sides of it.
            ...(special.length > 0 && options.length > 0
                ? [<Separator key="__special-separator" className="smart-select__separator" />]
                : []),
            ...options.map((option) => (
                <ListBox.Item
                    key={option.id}
                    id={option.id}
                    textValue={[option.name, option.description].filter(Boolean).join(" ")}
                >
                    {option.description ? (
                        <div className="flex min-w-0 flex-1 flex-col">
                            {/* Wrapping labels: the panel's width is capped,
                                so a long value continues on the next line
                                instead of being clipped. */}
                            <span className="wrap-anywhere">{option.name}</span>
                            <span className="text-muted text-xs wrap-anywhere">
                                {option.description}
                            </span>
                        </div>
                    ) : (
                        <span className="min-w-0 flex-1 wrap-anywhere">{option.name}</span>
                    )}
                    <ListBox.ItemIndicator />
                </ListBox.Item>
            )),
        ];
    }, [
        allOption?.count,
        allOption?.label,
        allOption?.present,
        nullOption?.count,
        nullOption?.label,
        nullOption?.present,
        options,
    ]);

    const renderValue = () => {
        if (compactLabel) {
            return isCompact || alwaysCompact ? compactLabel : renderFullValue();
        }
        return renderFullValue();
    };

    const renderFullValue = () => {
        // The pinned rows are values too when selected: a sentinel value
        // («__all» = «Все блоки») matches no option, yet the trigger must show
        // the row's label rather than the muted placeholder.
        if (!isMultiple && selectedSingle === ALL_OPTION_ID && allOption?.present) {
            return specialLabel(allOption);
        }
        if (isMultiple) {
            const names = selectedMultiple
                .map((id) => options.find((option) => option.id === id)?.name)
                .filter((name): name is string => Boolean(name));
            if (names.length === 0) return null;
            return names.join(", ");
        }
        const selected = options.find((option) => option.id === selectedSingle);
        if (!selected) return null;
        return selected.description ? (
            <span className="flex min-w-0 flex-col">
                <span className="truncate">{selected.name}</span>
                <span className="text-muted truncate text-xs">{selected.description}</span>
            </span>
        ) : (
            selected.name
        );
    };

    return (
        <div
            ref={wrapperRef}
            data-smart-select-open={isOpen || undefined}
            onMouseEnter={openFromHover}
            className={[
                // `--has-value`: the trigger shows a real value (an option OR a
                // pinned row) — react-aria flags a sentinel value as a
                // placeholder, which would grey the label out.
                renderValue() !== null && "smart-select--has-value",
                embedded && "smart-select--embedded",
            ]
                .filter(Boolean)
                .join(" ")}
            {...(embedded && {
                onKeyDown: (event: React.KeyboardEvent) => {
                    if (event.key !== "Escape") return;
                    event.stopPropagation();
                    onDismiss?.();
                },
            })}
        >
            {useAutocomplete ? (
                <Autocomplete
                    fullWidth={fullWidth}
                    variant={variant}
                    isDisabled={isDisabled}
                    placeholder={placeholder}
                    aria-label={ariaLabel ?? label ?? placeholder}
                    selectionMode={isMultiple ? "multiple" : "single"}
                    value={isMultiple ? selectedMultiple : selectedSingle}
                    onChange={(keys) =>
                        isMultiple ? emitMultiple(keys as Key[]) : emitSingle(keys as Key | null)
                    }
                    isOpen={isOpen}
                    onOpenChange={setIsOpen}
                >
                    {label ? <Label>{label}</Label> : null}
                    <Autocomplete.Trigger
                        {...(anchor && {
                            className: "column-filter__anchor",
                            style: { left: anchor.left, top: anchor.top, width: anchor.width },
                        })}
                        {...(embedded && {
                            className: "grid-autocomplete-editor__hidden-trigger",
                        })}
                    >
                        <Autocomplete.Value>
                            {() =>
                                renderValue() ?? <span className="text-muted">{placeholder}</span>
                            }
                        </Autocomplete.Value>
                        <Autocomplete.Indicator />
                    </Autocomplete.Trigger>
                    <Autocomplete.Popover
                        ref={popoverRef}
                        {...(anchor && { placement: ANCHOR_PLACEMENT, offset: ANCHOR_OFFSET })}
                        className={["smart-select-popover", popoverClassName]
                            .filter(Boolean)
                            .join(" ")}
                    >
                        {renderListbox ? (
                            <Autocomplete.Filter filter={contains}>
                                <SearchField
                                    autoFocus
                                    name="smart-select-search"
                                    aria-label={label ?? placeholder}
                                    variant="secondary"
                                >
                                    <SearchField.Group>
                                        <SearchField.SearchIcon />
                                        <SearchField.Input placeholder="Найти…" />
                                        <SearchField.ClearButton />
                                    </SearchField.Group>
                                </SearchField>
                                {renderListbox({ searchable: true })}
                            </Autocomplete.Filter>
                        ) : (
                            <Autocomplete.Filter filter={contains}>
                                <SearchField
                                    autoFocus
                                    name="smart-select-search"
                                    aria-label={label ?? placeholder}
                                    variant="secondary"
                                >
                                    <SearchField.Group>
                                        <SearchField.SearchIcon />
                                        <SearchField.Input placeholder="Найти…" />
                                        <SearchField.ClearButton />
                                    </SearchField.Group>
                                </SearchField>
                                <ListBox
                                    renderEmptyState={() => (
                                        <EmptyState>Ничего не найдено</EmptyState>
                                    )}
                                >
                                    {renderedOptions}
                                </ListBox>
                            </Autocomplete.Filter>
                        )}
                        {popoverFooter ?? null}
                    </Autocomplete.Popover>
                </Autocomplete>
            ) : (
                <Select
                    fullWidth={fullWidth}
                    variant={variant}
                    isDisabled={isDisabled}
                    placeholder={placeholder}
                    aria-label={ariaLabel ?? label ?? placeholder}
                    selectionMode={isMultiple ? "multiple" : "single"}
                    value={isMultiple ? selectedMultiple : selectedSingle}
                    onChange={(keys) =>
                        isMultiple ? emitMultiple(keys as Key[]) : emitSingle(keys as Key | null)
                    }
                    isOpen={isOpen}
                    onOpenChange={setIsOpen}
                >
                    {label ? <Label>{label}</Label> : null}
                    <Select.Trigger
                        {...(anchor && {
                            className: "column-filter__anchor",
                            style: { left: anchor.left, top: anchor.top, width: anchor.width },
                        })}
                        {...(embedded && {
                            className: "grid-autocomplete-editor__hidden-trigger",
                        })}
                    >
                        <Select.Value>
                            {() =>
                                renderValue() ?? <span className="text-muted">{placeholder}</span>
                            }
                        </Select.Value>
                        <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover
                        ref={popoverRef}
                        {...(anchor && { placement: ANCHOR_PLACEMENT, offset: ANCHOR_OFFSET })}
                        className={["smart-select-popover", popoverClassName]
                            .filter(Boolean)
                            .join(" ")}
                    >
                        {renderListbox ? (
                            renderListbox({ searchable: false })
                        ) : (
                            <ListBox>{renderedOptions}</ListBox>
                        )}
                        {popoverFooter ?? null}
                    </Select.Popover>
                </Select>
            )}
        </div>
    );
}
