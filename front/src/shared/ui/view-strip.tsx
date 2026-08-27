"use client";

import { Button, Dropdown, Label, Spinner } from "@heroui/react";
import { Check, Collapse, Download, Expand, MoreHoriz, Refresh } from "iconoir-react";
import { type Key, type ReactNode } from "react";

import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/**
 * The right-aligned control cluster of the toolbar: an expanded-on-hover
 * download button (hover-open «всю таблицу» / «видимую таблицу») and a
 * «Вид таблицы» button whose hover menu carries the display toggles (pin
 * the header, show service columns — as checked entries), the resets and the
 * fullscreen action (success). Both buttons start collapsed (icon only) and
 * expand on hover; the expand state is held while their dropdown is open.
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

    /** Down-arrow glyph shown for a disabled-spinner download trigger. */
    const collapsedDownloadButton = (
        isOpen: boolean,
        pending: boolean,
        handlers: { ref: React.Ref<HTMLButtonElement>; onMouseEnter: () => void },
    ) => (
        <Button
            ref={handlers.ref}
            onMouseEnter={handlers.onMouseEnter}
            className={isOpen ? "action-button action-button--open" : "action-button"}
            variant="tertiary"
            isDisabled={pending || isFullscreenPending}
            aria-label="Скачать в Excel"
        >
            <span className="action-button__icon">
                {pending ? <Spinner size="sm" /> : <Download aria-hidden="true" />}
            </span>
            <span className="action-button__label">
                {pending ? "Выгрузка..." : "Скачать в Excel"}
            </span>
        </Button>
    );

    const collapsedViewButton = (
        isOpen: boolean,
        handlers: { ref: React.Ref<HTMLButtonElement>; onMouseEnter: () => void },
    ) => (
        <Button
            ref={handlers.ref}
            onMouseEnter={handlers.onMouseEnter}
            className={isOpen ? "action-button action-button--open" : "action-button"}
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
                trigger={({ ref, onMouseEnter, isOpen }) =>
                    collapsedDownloadButton(isOpen, exportPending, { ref, onMouseEnter })
                }
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
            <HoverDropdown
                className="view-strip-popover"
                trigger={({ ref, onMouseEnter, isOpen }) =>
                    collapsedViewButton(isOpen, { ref, onMouseEnter })
                }
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
                        {menuItem(
                            "reset-column-widths",
                            "Сброс ширины столбцов",
                            false,
                            true,
                            <Refresh aria-hidden="true" />,
                        )}
                        {menuItem(
                            "reset-filters-sorting",
                            "Сброс всех фильтров и сортировок",
                            false,
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

    /** Toggle/reset entry. Toggles render a check when active; reset entries
        render their explicit icon (width/filters reset) instead. */
    function menuItem(id: string, label: string, active = false, danger = false, icon?: ReactNode) {
        return (
            <Dropdown.Item id={id} textValue={label} variant={danger ? "danger" : undefined}>
                <span className="view-strip-menu-check">
                    {icon ?? (active ? <Check aria-hidden="true" /> : null)}
                </span>
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
    }
}
