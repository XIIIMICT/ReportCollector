"use client";

import { Button, Dropdown, Spinner, Switch } from "@heroui/react";
import { Collapse, Download, Expand, Refresh } from "iconoir-react";
import type { ReactNode } from "react";

/**
 * One shared strip between the toolbar and the grid: display-only controls.
 * Left — optional filters plus the «Закрепить заголовок» / «Показать служебные
 * столбцы» checkboxes; right — the Excel export dropdown («вся» / «видимая»
 * таблица), the reset dropdown (column widths / all filters & sorting) and the
 * fullscreen toggle, identical in every project. Actions belong to the bottom
 * panel; everything display-related lives here.
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
    const hasToggles =
        headerPinned !== undefined &&
        onHeaderPinnedChange !== undefined &&
        showServiceColumns !== undefined &&
        onShowServiceColumnsChange !== undefined;

    return (
        <div className="view-strip">
            <div className="view-strip__filters">
                {filters}
                {hasToggles ? (
                    <div className="view-strip__toggles">
                        {/* Full compound structure: without Content/Control/
                            Thumb HeroUI renders only the plain label text and
                            nothing is clickable. */}
                        <Switch isSelected={headerPinned} onChange={onHeaderPinnedChange}>
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                Закрепить заголовок
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
                                Показать служебные столбцы
                            </Switch.Content>
                        </Switch>
                    </div>
                ) : null}
            </div>
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
        </div>
    );
}
