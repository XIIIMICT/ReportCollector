"use client";

import { Button, Dropdown, Label, Tooltip } from "@heroui/react";
import { Minus, Plus } from "iconoir-react";
import type { CSSProperties } from "react";

import { HiddenColumnsList, type HiddenColumnEntry } from "./hidden-columns-list";

/** What a right-click on a header asked to hide. */
export type GridHideRequest = {
    /** Screen coordinates of the pointer. */
    x: number;
    y: number;
    /** Leaf props to hide. */
    props: string[];
    /** Header level the clicked cell starts at (the expand button's top). */
    level: number;
    /** The target is the project-wide service-column block, not a data column. */
    service?: boolean;
};

/**
 * The header context menu — HeroUI's Dropdown, opened at the pointer.
 *
 * A zero-size fixed trigger is parked at the right-click coordinates; the
 * Dropdown's own popover brings the enter/exit animation, focus management and
 * keyboard navigation. The menu is a single item for now: «Скрыть».
 */
export function GridHeaderMenu({
    request,
    isOpen,
    onOpenChange,
    onHide,
}: {
    request: GridHideRequest | null;
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
    onHide: (request: GridHideRequest) => void;
}) {
    if (!request) return null;
    return (
        <Dropdown isOpen={isOpen} onOpenChange={onOpenChange}>
            <Dropdown.Trigger
                className="grid-header-menu__anchor"
                style={{ left: request.x, top: request.y }}
            >
                <span />
            </Dropdown.Trigger>
            <Dropdown.Popover
                placement="bottom start"
                className="grid-header-menu__popover min-w-40"
            >
                <Dropdown.Menu
                    aria-label="Действия со столбцом"
                    onAction={() => {
                        onHide(request);
                        onOpenChange(false);
                    }}
                >
                    <Dropdown.Item id="hide-column" textValue="Скрыть">
                        <Minus aria-hidden="true" />
                        <Label>Скрыть</Label>
                    </Dropdown.Item>
                </Dropdown.Menu>
            </Dropdown.Popover>
        </Dropdown>
    );
}

/**
 * The icon-only expand button standing where a hidden block used to start.
 *
 * HeroUI's `Button` (icon-only), positioned by the grid over its header. The
 * CSS centres it on the hidden block's left boundary.
 */
export function GridUnhideButton({
    label,
    entries,
    onPress,
    style,
}: {
    label: string;
    entries: readonly HiddenColumnEntry[];
    onPress: () => void;
    style?: CSSProperties;
}) {
    return (
        <span className="grid-unhide-anchor" {...(style !== undefined && { style })}>
            <Tooltip delay={200}>
                <Button
                    isIconOnly
                    size="sm"
                    variant="secondary"
                    aria-label={label}
                    className="grid-unhide-button"
                    onPress={onPress}
                >
                    <Plus aria-hidden="true" />
                </Button>
                <Tooltip.Content placement="top" className="grid-unhide-tooltip">
                    <HiddenColumnsList entries={entries} />
                </Tooltip.Content>
            </Tooltip>
        </span>
    );
}
