"use client";

import { Button, Dropdown, Label } from "@heroui/react";
import {
    ArrowSeparateVertical,
    ArrowUnionVertical,
    Collapse,
    Expand,
    Eye,
    EyeClosed,
    MoreHoriz,
    Pin,
    PinSlash,
    Refresh,
} from "iconoir-react";
import { type Key, type ReactNode } from "react";

import { HoverDropdown } from "@/shared/ui/hover-dropdown";
import { DEFAULT_ROW_HEIGHT, MIN_ROW_HEIGHT } from "@/shared/ui/use-view-state";

/**
 * The right-aligned control cluster of the toolbar: the «Вид таблицы» button
 * whose menu carries the display toggles (pin the header, show service
 * columns — as checked entries), the resets and the fullscreen action
 * (success). Excel export is NOT here: it lives in the bottom action bar, one
 * button per form. The button is expanded by default and collapses at the same
 * breakpoint as the bottom action bar.
 */
export function ViewStrip({
    headerPinned,
    onHeaderPinnedChange,
    showServiceColumns,
    onShowServiceColumnsChange,
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
    showServiceColumns?: boolean;
    onShowServiceColumnsChange?: (value: boolean) => void;
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
    const hasToggles =
        headerPinned !== undefined &&
        onHeaderPinnedChange !== undefined &&
        showServiceColumns !== undefined &&
        onShowServiceColumnsChange !== undefined;
    const hasRowHeight = onRowHeightChange !== undefined;

    const viewButton = (
        isOpen: boolean,
        handlers: {
            ref: React.Ref<HTMLButtonElement>;
            onMouseEnter: () => void;
            onPress: () => void;
        },
    ) => (
        <Button
            ref={handlers.ref}
            onMouseEnter={handlers.onMouseEnter}
            onPress={handlers.onPress}
            className={
                isOpen
                    ? "action-button action-button--open view-strip__view-button"
                    : "action-button view-strip__view-button"
            }
            variant="tertiary"
            aria-label="Вид таблицы"
        >
            <span className="action-button__icon">
                <MoreHoriz aria-hidden="true" />
            </span>
            <span className="action-button__label">Вид таблицы</span>
        </Button>
    );

    return (
        <div className="view-strip__actions">
            <HoverDropdown
                className="view-strip-popover"
                {...(onDropdownOpenChange && { onOpenChange: onDropdownOpenChange })}
                trigger={({ ref, onMouseEnter, isOpen, onPress }) =>
                    viewButton(isOpen, { ref, onMouseEnter, onPress })
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
                                      <ArrowSeparateVertical aria-hidden="true" />
                                  ) : (
                                      <ArrowUnionVertical aria-hidden="true" />
                                  ),
                              )
                            : null}
                        {hasToggles
                            ? menuItem(
                                  "service-columns",
                                  showServiceColumns
                                      ? "Скрыть данные о редактировании строк"
                                      : "Показать данные о редактировании строк",
                                  false,
                                  showServiceColumns ? (
                                      <EyeClosed aria-hidden="true" />
                                  ) : (
                                      <Eye aria-hidden="true" />
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
            case "service-columns":
                onShowServiceColumnsChange?.(!showServiceColumns);
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
