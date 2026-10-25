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
import { Check, Refresh, Xmark } from "iconoir-react";
import type { Key, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { ExpandableButton } from "@/shared/ui/expandable-button";
import { ExpandableButtonGroup } from "@/shared/ui/expandable-button-group";

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
 *
 * `closeOnUnhover={false}` turns the control into COMMIT mode: hover never
 * dismisses it, the popover stays pinned until the user decides, and an
 * icon-only «По умолчанию / Отменить / Применить» group sits at the bottom
 * (outside the scrolling list). Selections still report through `onChange`
 * (the owner previews them) — «Применить» (or Enter) calls `onConfirm`;
 * «Отменить», Escape and an outside click call `onCancel`, which restores the
 * value captured before opening; «По умолчанию» calls `onReset` (when given).
 *
 * An EMBEDDED control (an in-cell editor) gets the same bar without becoming
 * commit mode: it still closes on the grid's own dismissal (Escape / an
 * outside press on the cell), and the editor owns what «Отмена» and
 * «Применить» mean — `confirmEnabled` says whether there is something to
 * apply. The bar is only shown when there IS something to commit: the filter's
 * commit mode or a MULTI-select editor (a single pick saves on the spot, so
 * two buttons would only be in the way). It lives here once — the splitter
 * rule, the expanding buttons and the width their labels need while one is
 * hovered — so a filter and an editor look alike.
 */

export type SmartSelectOption = {
    id: string;
    name: string;
    /** A muted second line inside the option and the trigger. */
    description?: string;
};

/** Stable empty default: an inline `[]` would be a new array on every render. */
const NO_OPTIONS: SmartSelectOption[] = [];

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
    /** Controlled lifecycle for owners that wait for the exit animation. */
    isOpen?: boolean;
    popoverRef?: React.Ref<HTMLElement>;
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
    /**
     * `"builtin"` — `Autocomplete.Filter` narrows the items by the query.
     * `"custom"` — the listbox renders the search field itself and filters its
     * own window (the column filter needs the query for «Выбрать найденные»,
     * and its window is paged, so the wrapper's item filtering is wrong anyway).
     */
    searchMode?: "builtin" | "custom";
    /** Always render the searchable control regardless of the option count. */
    forceSearchable?: boolean;
    /** The trigger is an invisible anchor at (left, top) — the column filter. */
    anchor?: { left: number; top: number; width: number };
    /** Extra class on the popover. */
    popoverClassName?: string;
    /** Open the popover on mount (the column filter's anchor popover). */
    defaultOpen?: boolean;
    /**
     * Close after the pointer leaves the trigger and popover (default: true).
     * `false` switches to commit mode with the «Отменить / Применить» footer.
     */
    closeOnUnhover?: boolean;
    /** Commit mode: «Применить» — keep the pending selection. */
    onConfirm?: () => void;
    /** Commit mode: «Отменить», Escape, or an outside click — restore the old value. */
    onCancel?: () => void;
    /** Commit mode: reveal «Применить» (the filter has a pending change). */
    canConfirm?: boolean;
    /**
     * Action bar «Применить» is enabled. In a multi-select editor a pick only
     * builds the pending set, so the bar shows only when the set changed.
     */
    confirmEnabled?: boolean;
    /** Label of the action bar's dismissive button («Отменить» by default). */
    cancelLabel?: string;
    /** Label of the action bar's forward button («Применить» by default). */
    confirmLabel?: string;
    /**
     * Commit mode: «По умолчанию» — reset this control to its default (the
     * column filter's stored default, not the value before opening). The
     * button renders only when the handler is given.
     */
    onReset?: () => void;
    /** Accessible name of «По умолчанию» (icon-only button). */
    resetLabel?: string;
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
    options = NO_OPTIONS,
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
    searchMode = "builtin",
    anchor,
    popoverClassName,
    defaultOpen = false,
    isOpen: controlledOpen,
    popoverRef: externalPopoverRef,
    closeOnUnhover = true,
    onConfirm,
    onCancel,
    canConfirm = true,
    confirmEnabled,
    cancelLabel = "Отменить",
    confirmLabel = "Применить",
    onReset,
    resetLabel = "По умолчанию",
}: SmartSelectProps) {
    // Commit mode (the column filter): the popover stays open until the user
    // picks «Применить» or «Отменить».
    const isCommitMode = !embedded && !closeOnUnhover;
    const { contains } = useFilter({ sensitivity: "base" });
    // The toolbar shows a short label instead of the value on narrow screens.
    // `lg` (64rem) matches HeroUI's breakpoint scale; `sm` is 40rem.
    const isCompact = useMediaQuery(compactBelow === "sm" ? "(width < 40rem)" : "(width < 64rem)");
    // An embedded editor is open from the first paint (it IS the cell editor);
    // the column filter's anchor popover opens on mount too.
    const [openState, setIsOpen] = useState(embedded || defaultOpen);
    const isOpen = controlledOpen ?? openState;
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
    // The same for the commit-mode callbacks: the document listeners below are
    // bound once per mode change, yet must call the LATEST handler.
    const onConfirmRef = useRef(onConfirm);
    onConfirmRef.current = onConfirm;
    const onCancelRef = useRef(onCancel);
    onCancelRef.current = onCancel;

    // The commit callbacks are re-created per render at the call sites, so the
    // document listeners read the current behaviour through refs and stay
    // identity-stable. «Применить» keeps the pending value, «Отменить» closes
    // first (react-aria then skip their dismiss side effects) and restores.
    const confirmRef = useRef<() => void>(() => {});
    confirmRef.current = () => {
        if (openRef.current) onConfirmRef.current?.();
    };
    const cancelRef = useRef<() => void>(() => {});
    cancelRef.current = () => {
        if (!openRef.current) return;
        setIsOpen(false);
        onCancelRef.current?.();
    };
    const resetRef = useRef(onReset);
    resetRef.current = onReset;
    const canConfirmRef = useRef(canConfirm);
    canConfirmRef.current = canConfirm;
    // The bar's own enablement: a multi-select editor's pending set wins, the
    // commit-mode flag («По умолчанию / Отменить / Применить» of the filter) is
    // the fallback.
    const barConfirmEnabled = confirmEnabled ?? canConfirm;

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
    // In commit mode Escape dismisses WITHOUT applying («Отменить»).
    useEffect(() => {
        if (embedded) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Escape" || !openRef.current) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (isCommitMode) cancelRef.current();
            else setIsOpen(false);
        };
        document.addEventListener("keydown", onKeyDown, true);
        return () => document.removeEventListener("keydown", onKeyDown, true);
    }, [embedded, isCommitMode]);

    // Commit mode: Enter anywhere in the filter means «Применить». Registered
    // like Escape (document capture ⇒ before react-aria's own key handling).
    useEffect(() => {
        if (!isCommitMode) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Enter" || !openRef.current) return;
            // A modifier combination (Ctrl+Enter, Alt+Enter) is not a commit.
            if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
            if (!isCommitKeyTarget(event)) return;
            event.preventDefault();
            event.stopPropagation();
            if (canConfirmRef.current) confirmRef.current();
        };
        document.addEventListener("keydown", onKeyDown, true);
        return () => document.removeEventListener("keydown", onKeyDown, true);
    }, [isCommitMode]);

    // Commit mode: an outside click cancels. react-aria's own outside press only
    // closes the popover, so the owner would neither apply nor restore — the
    // footer therefore defaults to «Отменить». The press lands on the underlay
    // (the popover's backdrop), never on the list or the footer themselves.
    useEffect(() => {
        if (!isCommitMode) return;
        const onPointerDown = (event: Event) => {
            if (!openRef.current) return;
            const target = event.target;
            if (!(target instanceof Element) || !target.closest("[data-testid='underlay']")) return;
            cancelRef.current();
        };
        // HeroUI listens for pointer presses; the mouse fallback keeps the
        // behaviour in environments without PointerEvent (jsdom, old browsers).
        document.addEventListener("pointerdown", onPointerDown, true);
        document.addEventListener("mousedown", onPointerDown, true);
        return () => {
            document.removeEventListener("pointerdown", onPointerDown, true);
            document.removeEventListener("mousedown", onPointerDown, true);
        };
    }, [isCommitMode]);

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

    // Every field of the two special options is listed in the dependency array
    // below, which is the precise dependency; the linter wants the objects
    // themselves, but callers build them inline and object identity would rebuild
    // the whole list on every render.
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

    // A keydown inside the trigger, the open panel (its search field, list) or
    // the footer. The document-level listener fires before react-aria acts on
    // the key, so Enter can be claimed for «Применить» before the list or the
    // search field consumes it.
    function isCommitKeyTarget(event: KeyboardEvent): boolean {
        const target = event.target;
        if (!(target instanceof Node)) return false;
        return Boolean(
            popoverRef.current?.contains(target) || wrapperRef.current?.contains(target),
        );
    }

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

    // One action bar for every owner that COMMITS A SET rather than picking a
    // value: the column filter (commit mode) and a multi-select in-cell editor.
    // A single-value pick saves on the spot and gets no bar — two buttons
    // would only be in the way. The buttons stand under the list, outside its
    // scroll, and expand on hover down to their icons — the same group the
    // toolbar uses, so the labels' reserved width is measured once here. The
    // rule above the bar is the column filter's splitter: the bar is a
    // separate zone of the panel, not the last list row.
    const showActionBar = isCommitMode || (embedded && multiple);
    const actionBar = showActionBar ? (
        <div
            className="smart-select__action-bar"
            data-react-aria-prevent-focus
            // Enter belongs to the control and means «Применить».
            onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                event.stopPropagation();
                if (barConfirmEnabled) confirmRef.current();
            }}
        >
            <Separator className="smart-select__footer-separator" />
            <div className="smart-select__footer">
                <ExpandableButtonGroup size="sm" reserveLast={2}>
                    {/* Direct children only: the group injects props into them. */}
                    {onReset ? (
                        <ExpandableButton
                            variant="danger"
                            aria-label={resetLabel}
                            icon={<Refresh aria-hidden="true" />}
                            onPress={() => resetRef.current?.()}
                        >
                            По умолчанию
                        </ExpandableButton>
                    ) : null}
                    <ExpandableButton
                        variant="tertiary"
                        aria-label={cancelLabel}
                        icon={<Xmark aria-hidden="true" />}
                        onPress={() => cancelRef.current()}
                    >
                        {cancelLabel}
                    </ExpandableButton>
                    <ExpandableButton
                        variant="primary"
                        aria-label={confirmLabel}
                        icon={<Check aria-hidden="true" />}
                        isDisabled={!barConfirmEnabled}
                        onPress={() => confirmRef.current()}
                    >
                        {confirmLabel}
                    </ExpandableButton>
                </ExpandableButtonGroup>
            </div>
        </div>
    ) : null;

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
                isCommitMode && "smart-select--commit",
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
                    onOpenChange={(open) => {
                        setIsOpen(open);
                        if (controlledOpen !== undefined && open !== controlledOpen)
                            onOpenChangeRef.current?.(open);
                    }}
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
                        isExiting={controlledOpen === false}
                        ref={(element) => {
                            popoverRef.current = element;
                            if (typeof externalPopoverRef === "function")
                                externalPopoverRef(element);
                            else if (externalPopoverRef) externalPopoverRef.current = element;
                        }}
                        {...(anchor && { placement: ANCHOR_PLACEMENT, offset: ANCHOR_OFFSET })}
                        className={["smart-select-popover", popoverClassName]
                            .filter(Boolean)
                            .join(" ")}
                    >
                        {renderListbox ? (
                            searchMode === "custom" ? (
                                renderListbox({ searchable: true })
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
                                    {renderListbox({ searchable: true })}
                                </Autocomplete.Filter>
                            )
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
                        {actionBar}
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
                    onOpenChange={(open) => {
                        setIsOpen(open);
                        if (controlledOpen !== undefined && open !== controlledOpen)
                            onOpenChangeRef.current?.(open);
                    }}
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
                        isExiting={controlledOpen === false}
                        ref={(element) => {
                            popoverRef.current = element;
                            if (typeof externalPopoverRef === "function")
                                externalPopoverRef(element);
                            else if (externalPopoverRef) externalPopoverRef.current = element;
                        }}
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
                        {actionBar}
                    </Select.Popover>
                </Select>
            )}
        </div>
    );
}
