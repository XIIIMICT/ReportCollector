"use client";

import { Dropdown, Label } from "@heroui/react";
import {
    Collapse,
    CompressLines,
    Expand,
    ExpandLines,
    MoreHoriz,
    Pin,
    PinSlash,
    Refresh,
} from "iconoir-react";
import { type Key, type ReactNode } from "react";

import { ExpandableButton } from "@/shared/ui/expandable-button";
import { HoverDropdown } from "@/shared/ui/hover-dropdown";
import { DEFAULT_ROW_HEIGHT, MIN_ROW_HEIGHT } from "@/shared/ui/use-view-state";

/**
 * The right-aligned control cluster of the toolbar: the «Вид таблицы» button
 * whose menu carries the display toggles (pin the header — as a checked
 * entry), the resets and the fullscreen action (success). Service columns are
 * NOT here any more: they are revealed by the header's + button, project-wide.
 * Excel export is NOT here: it lives in the bottom action bar, one
 * button per form. The button is expanded by default and collapses at the same
 * breakpoint as the bottom action bar.
 */
export function ViewStrip({
    headerPinned,
    onHeaderPinnedChange,
    rowHeight,
    onRowHeightChange,
    onResetRowHeight,
    onReset,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
    onDropdownOpenChange,
    columnResetLabel = "Сбросить ширину столбцов и высоту строк",
}: {
    headerPinned?: boolean;
    onHeaderPinnedChange?: (value: boolean) => void;
    rowHeight?: number;
    onRowHeightChange?: (value: number) => void;
    onResetRowHeight?: () => void;
    onReset: (scope: "columnWidths" | "filtersSorting") => void;
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
    /** Notifies the owner when a hover dropdown opens/closes (holds the
        fullscreen toolbar panels open while its menu is expanded). */
    onDropdownOpenChange?: (open: boolean) => void;
    /** Label of the column-reset entry (the plan also resets header heights). */
    columnResetLabel?: string;
}) {
    const hasToggles = headerPinned !== undefined && onHeaderPinnedChange !== undefined;
    const hasRowHeight = onRowHeightChange !== undefined;

    const viewButton = (
        isOpen: boolean,
        handlers: {
            ref: React.Ref<HTMLButtonElement>;
            onHoverStart: () => void;
            onPress: () => void;
        },
    ) => (
        <ExpandableButton
            ref={handlers.ref}
            onHoverStart={handlers.onHoverStart}
            onPress={handlers.onPress}
            className="view-strip__view-button"
            variant="tertiary"
            aria-label="Вид таблицы"
            icon={<MoreHoriz aria-hidden="true" />}
            isExpanded={isOpen}
        >
            Вид таблицы
        </ExpandableButton>
    );

    return (
        <div className="view-strip__actions">
            <HoverDropdown
                className="view-strip-popover"
                {...(onDropdownOpenChange && { onOpenChange: onDropdownOpenChange })}
                trigger={({ ref, onHoverStart, isOpen, onPress }) =>
                    viewButton(isOpen, { ref, onHoverStart, onPress })
                }
                content={
                    <Dropdown.Menu
                        aria-label="Вид таблицы"
                        onAction={handleMenuAction}
                        className="action-more-menu"
                    >
                        {hasToggles
                            ? menuItem(
                                  "pin-header",
                                  headerPinned
                                      ? "Открепить шапку таблицы"
                                      : "Закрепить шапку таблицы",
                                  false,
                                  headerPinned ? (
                                      <PinSlash aria-hidden="true" />
                                  ) : (
                                      <Pin aria-hidden="true" />
                                  ),
                              )
                            : null}
                        {hasRowHeight
                            ? menuItem(
                                  "row-density",
                                  rowHeight === MIN_ROW_HEIGHT
                                      ? "Расширить строки"
                                      : "Сузить строки",
                                  false,
                                  rowHeight === MIN_ROW_HEIGHT ? (
                                      <ExpandLines aria-hidden="true" />
                                  ) : (
                                      <CompressLines aria-hidden="true" />
                                  ),
                              )
                            : null}
                        {menuItem(
                            "reset-column-widths",
                            columnResetLabel,
                            true,
                            <Refresh aria-hidden="true" />,
                        )}
                        {menuItem(
                            "reset-filters-sorting",
                            "Сбросить все фильтры и сортировки",
                            true,
                            <Refresh aria-hidden="true" />,
                        )}
                        <Dropdown.Item
                            id="fullscreen"
                            textValue={isFullscreen ? "Выйти из полного экрана" : "В полный экран"}
                        >
                            <span className="view-strip-menu-check">
                                {isFullscreen ? (
                                    <Collapse aria-hidden="true" />
                                ) : (
                                    <Expand aria-hidden="true" />
                                )}
                            </span>
                            <Label>
                                {isFullscreen ? "Выйти из полного экрана" : "В полный экран"}
                            </Label>
                        </Dropdown.Item>
                    </Dropdown.Menu>
                }
            />
        </div>
    );

    /**
     * Menu entry. Toggles always carry an explicit icon (pin / pin-slash,
     * eye / eye-closed) so the entry says what a press will DO — the label
     * changes with the state too. Reset entries pass their own icon.
     */
    function menuItem(id: string, label: string, danger = false, icon?: ReactNode) {
        return (
            <Dropdown.Item id={id} textValue={label} variant={danger ? "danger" : undefined}>
                <span className="view-strip-menu-check">{icon}</span>
                <Label>{label}</Label>
            </Dropdown.Item>
        );
    }

    function handleMenuAction(key: Key) {
        switch (String(key)) {
            case "pin-header":
                onHeaderPinnedChange?.(!headerPinned);
                break;
            case "row-density":
                onRowHeightChange?.(
                    rowHeight === MIN_ROW_HEIGHT ? DEFAULT_ROW_HEIGHT : MIN_ROW_HEIGHT,
                );
                break;
            case "reset-column-widths":
                onReset("columnWidths");
                onResetRowHeight?.();
                break;
            case "reset-filters-sorting":
                onReset("filtersSorting");
                break;
            case "fullscreen":
                if (!isFullscreenPending) onToggleFullscreen();
                break;
        }
    }
}
