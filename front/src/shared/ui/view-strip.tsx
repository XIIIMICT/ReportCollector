"use client";

import { Button, Dropdown, Label, Spinner, Switch } from "@heroui/react";
import { Check, Collapse, Download, Expand, MoreVert, Refresh } from "iconoir-react";
import { type Key, type ReactNode } from "react";

import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/**
 * One shared strip between the toolbar and the grid: display-only controls.
 * Left — optional filters plus the «Закрепить заголовок» / «Показать служебные
 * столбцы» switches; right — the Excel export dropdown («вся» / «видимая»
 * таблица), the reset dropdown (column widths / all filters & sorting) and the
 * fullscreen toggle, identical in every project. Actions belong to the bottom
 * panel; everything display-related lives here.
 *
 * At md and below the switches and all right-side controls collapse into a
 * single hover menu («Настройки таблицы»), mirroring the bottom action bar's
 * compact «Действия» dropdown.
 */
export function ViewStrip({
    filters = null,
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
    filters?: ReactNode;
    /** Both toggle pairs are optional: pass them where the grid supports them. */
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
    const isCompact = useMediaQuery("(width < 48rem)");
    const hasToggles =
        headerPinned !== undefined &&
        onHeaderPinnedChange !== undefined &&
        showServiceColumns !== undefined &&
        onShowServiceColumnsChange !== undefined;

    const handleMenuAction = (key: Key) => {
        switch (String(key)) {
            case "pin-header":
                onHeaderPinnedChange?.(!headerPinned);
                break;
            case "service-columns":
                onShowServiceColumnsChange?.(!showServiceColumns);
                break;
            case "export-all":
                onExport("all");
                break;
            case "export-visible":
                onExport("visible");
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

    /** Menu item with a leading check column (for the toggle entries). */
    const menuItem = (id: string, label: string, active = false, icon?: ReactNode) => (
        <Dropdown.Item id={id} textValue={label}>
            <span className="view-strip-menu-check">
                {active ? <Check aria-hidden="true" /> : (icon ?? null)}
            </span>
            <Label>{label}</Label>
        </Dropdown.Item>
    );

    const compactMenu = (
        <HoverDropdown
            className="action-more-popover"
            trigger={({ ref, onMouseEnter }) => (
                <Button
                    ref={ref}
                    onMouseEnter={onMouseEnter}
                    className="action-more-button"
                    variant="tertiary"
                    aria-label="Настройки таблицы"
                >
                    <MoreVert aria-hidden="true" />
                    <span className="action-more-button__label">Настройки</span>
                </Button>
            )}
            content={
                <Dropdown.Menu
                    aria-label="Настройки таблицы"
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
                    {menuItem("export-all", "Скачать всю таблицу", false, <Download />)}
                    {menuItem("export-visible", "Скачать видимую таблицу", false, <Download />)}
                    {menuItem("reset-column-widths", "Сброс ширины столбцов", false, <Refresh />)}
                    {menuItem(
                        "reset-filters-sorting",
                        "Сброс всех фильтров и сортировок",
                        false,
                        <Refresh />,
                    )}
                    {menuItem(
                        "fullscreen",
                        isFullscreen ? "Выйти из полного экрана" : "В полный экран",
                        false,
                        isFullscreen ? <Collapse /> : <Expand />,
                    )}
                </Dropdown.Menu>
            }
        />
    );

    return (
        <div className="view-strip">
            <div className="view-strip__filters">
                {filters}
                {!isCompact && hasToggles ? (
                    <div className="view-strip__toggles">
                        {/* Full compound structure: without Content/Control/
                            Thumb HeroUI renders only the plain label text and
                            nothing is clickable. */}
                        <Switch isSelected={headerPinned} onChange={onHeaderPinnedChange}>
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                Закрепить <br className="view-strip__br" /> заголовок
                            </Switch.Content>
                        </Switch>
                        <Switch
                            isSelected={showServiceColumns}
                            onChange={onShowServiceColumnsChange}
                        >
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                Показать служебные <br className="view-strip__br" /> столбцы
                            </Switch.Content>
                        </Switch>
                    </div>
                ) : null}
            </div>
            {isCompact ? (
                compactMenu
            ) : (
                <div className="view-strip__actions">
                    <Dropdown>
                        <Button
                            className="action-button"
                            variant="secondary"
                            isDisabled={exportPending || isFullscreenPending}
                            aria-label="Скачать таблицу в Excel"
                        >
                            <span className="action-button__icon">
                                {exportPending ? <Spinner size="sm" /> : <Download />}
                            </span>
                            <span className="action-button__label">
                                {exportPending ? "Выгрузка..." : "Скачать в Excel"}
                            </span>
                        </Button>
                        <Dropdown.Popover>
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
                        </Dropdown.Popover>
                    </Dropdown>
                    <Dropdown>
                        <Button
                            className="action-button"
                            variant="danger"
                            isDisabled={isFullscreenPending}
                            aria-label="Сброс отображения таблицы"
                        >
                            <span className="action-button__icon">
                                <Refresh />
                            </span>
                            <span className="action-button__label">Сброс</span>
                        </Button>
                        <Dropdown.Popover>
                            <Dropdown.Menu
                                aria-label="Сброс отображения таблицы"
                                onAction={(key) => {
                                    if (key === "columnWidths" || key === "filtersSorting") {
                                        onReset(key);
                                    }
                                }}
                            >
                                <Dropdown.Item id="columnWidths" textValue="Сброс ширины столбцов">
                                    Сброс ширины столбцов
                                </Dropdown.Item>
                                <Dropdown.Item
                                    id="filtersSorting"
                                    textValue="Сброс всех фильтров и сортировок"
                                >
                                    Сброс всех фильтров и сортировок
                                </Dropdown.Item>
                            </Dropdown.Menu>
                        </Dropdown.Popover>
                    </Dropdown>
                    <Button
                        className="action-button"
                        variant="secondary"
                        isDisabled={isFullscreenPending}
                        aria-label={isFullscreen ? "Выйти из полного экрана" : "В полный экран"}
                        onPress={onToggleFullscreen}
                    >
                        <span className="action-button__icon">
                            {isFullscreen ? <Collapse /> : <Expand />}
                        </span>
                        <span className="action-button__label">
                            {isFullscreen ? "Выйти из полного экрана" : "В полный экран"}
                        </span>
                    </Button>
                </div>
            )}
        </div>
    );
}
