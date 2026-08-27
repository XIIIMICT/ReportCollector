"use client";

import { Button, Dropdown, Label, Spinner } from "@heroui/react";
import { Check, Collapse, Download, Expand, MoreHoriz } from "iconoir-react";
import { type Key } from "react";

import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/**
 * The right-aligned control cluster of the toolbar: an always-expanded
 * «Вид таблицы» button whose hover menu carries the display toggles (pin the
 * header, show service columns — as checked entries), the resets and the
 * fullscreen action (success), plus a separate download button (hover-open
 * «всю таблицу» / «видимую таблицу»). Everything opens on hover.
 */
export function ViewStrip({
    headerPinned,
    onHeaderPinnedChange,
    showServiceColumns,
    onShowServiceColumnsChange,
    exportPending,
    onExport,
    onReset,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
}: {
    headerPinned?: boolean;
    onHeaderPinnedChange?: (value: boolean) => void;
    showServiceColumns?: boolean;
    onShowServiceColumnsChange?: (value: boolean) => void;
    exportPending: boolean;
    onExport: (scope: "all" | "visible") => void;
    onReset: (scope: "columnWidths" | "filtersSorting") => void;
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
}) {
    const hasToggles =
        headerPinned !== undefined &&
        onHeaderPinnedChange !== undefined &&
        showServiceColumns !== undefined &&
        onShowServiceColumnsChange !== undefined;

    /** Toggle/reset/fullscreen entry with a leading check for active toggles. */
    const menuItem = (id: string, label: string, active = false, danger = false) => (
        <Dropdown.Item id={id} textValue={label} variant={danger ? "danger" : undefined}>
            <span className="view-strip-menu-check">
                {active ? <Check aria-hidden="true" /> : null}
            </span>
            <Label>{label}</Label>
        </Dropdown.Item>
    );

    const handleMenuAction = (key: Key) => {
        switch (String(key)) {
            case "pin-header":
                onHeaderPinnedChange?.(!headerPinned);
                break;
            case "service-columns":
                onShowServiceColumnsChange?.(!showServiceColumns);
                break;
            case "reset-column-widths":
                onReset("columnWidths");
                break;
            case "reset-filters-sorting":
                onReset("filtersSorting");
                break;
            case "fullscreen":
                if (!isFullscreenPending) onToggleFullscreen();
                break;
        }
    };

    return (
        <div className="view-strip__actions">
            <HoverDropdown
                className="view-strip-popover"
                trigger={({ ref, onMouseEnter }) => (
                    <Button
                        ref={ref}
                        onMouseEnter={onMouseEnter}
                        className="action-more-button"
                        variant="tertiary"
                        aria-label="Вид таблицы"
                    >
                        <MoreHoriz aria-hidden="true" />
                        <span className="action-more-button__label">Вид таблицы</span>
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        aria-label="Вид таблицы"
                        onAction={handleMenuAction}
                        className="action-more-menu"
                    >
                        {hasToggles
                            ? menuItem("pin-header", "Закрепить заголовок", headerPinned)
                            : null}
                        {hasToggles
                            ? menuItem(
                                  "service-columns",
                                  "Показать служебные столбцы",
                                  showServiceColumns,
                              )
                            : null}
                        {menuItem("reset-column-widths", "Сброс ширины столбцов", false, true)}
                        {menuItem(
                            "reset-filters-sorting",
                            "Сброс всех фильтров и сортировок",
                            false,
                            true,
                        )}
                        <Dropdown.Item
                            id="fullscreen"
                            textValue={isFullscreen ? "Выйти из полного экрана" : "В полный экран"}
                            // HeroUI ships only a `danger` MenuItem variant; success is
                            // reproduced the same way — color the label slot via the
                            // theme's `--success` token.
                            className="[&_[data-slot=label]]:text-success"
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
            <HoverDropdown
                className="view-strip-popover"
                trigger={({ ref, onMouseEnter }) => (
                    <Button
                        ref={ref}
                        onMouseEnter={onMouseEnter}
                        className="action-more-button"
                        variant="tertiary"
                        isDisabled={exportPending || isFullscreenPending}
                        aria-label="Скачать таблицу в Excel"
                    >
                        {exportPending ? <Spinner size="sm" /> : <Download aria-hidden="true" />}
                        <span className="action-more-button__label">
                            {exportPending ? "Выгрузка..." : "Скачать в Excel"}
                        </span>
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        aria-label="Формат выгрузки"
                        onAction={(key) => {
                            if (key === "all" || key === "visible") {
                                onExport(key);
                            }
                        }}
                    >
                        <Dropdown.Item id="all" textValue="Скачать всю таблицу">
                            Скачать всю таблицу
                        </Dropdown.Item>
                        <Dropdown.Item id="visible" textValue="Скачать видимую таблицу">
                            Скачать видимую таблицу
                        </Dropdown.Item>
                    </Dropdown.Menu>
                }
            />
        </div>
    );
}
