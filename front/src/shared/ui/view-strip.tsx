"use client";

import { Button, Dropdown, Spinner } from "@heroui/react";
import { Collapse, Download, Expand } from "iconoir-react";
import type { ReactNode } from "react";

/**
 * One shared strip between the toolbar and the grid: display-only controls.
 * Left — optional filters (the «Ремонт» plan asset/block pickers); right —
 * the Excel export dropdown («вся» / «видимая» таблица) and the fullscreen
 * toggle, identical in every project. Actions belong to the bottom panel;
 * everything display-related lives here.
 */
export function ViewStrip({
    filters = null,
    exportPending,
    onExport,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
}: {
    filters?: ReactNode;
    exportPending: boolean;
    onExport: (scope: "all" | "visible") => void;
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
}) {
    return (
        <div className="view-strip">
            <div className="view-strip__filters">{filters}</div>
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
