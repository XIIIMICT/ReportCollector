"use client";

import { Button, Dropdown, Label, Tooltip } from "@heroui/react";
import { Minus, Pin, PinSlash, Plus } from "iconoir-react";
import type { CSSProperties, ReactNode } from "react";

import { HiddenColumnsList, type HiddenColumnEntry } from "./hidden-columns-list";

/** One context-menu action. */
export type GridMenuItem = {
    id: string;
    label: string;
    icon: "hide" | "pin" | "unpin";
    onSelect: () => void;
};

/** A right-click anywhere in a grid: the pointer and the applicable actions. */
export type GridMenuRequest = {
    x: number;
    y: number;
    items: GridMenuItem[];
};

/**
 * The grid context menu — HeroUI Dropdown, opened at the pointer.
 *
 * A zero-size fixed trigger is parked at the right-click coordinates; the
 * Dropdown's own popover brings the enter/exit animation, focus management and
 * keyboard navigation. Header cells supply hide/pin actions, row numbers (and
 * the pinned-row copy) supply row pin actions.
 */
export function GridContextMenu({
    request,
    isOpen,
    onOpenChange,
}: {
    request: GridMenuRequest | null;
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    if (!request || request.items.length === 0) return null;
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
                    aria-label="Действия"
                    onAction={(key) => {
                        const item = request.items.find((entry) => entry.id === String(key));
                        if (!item) return;
                        item.onSelect();
                        onOpenChange(false);
                    }}
                >
                    {request.items.map((item) => (
                        <Dropdown.Item key={item.id} id={item.id} textValue={item.label}>
                            {menuIcon(item.icon)}
                            <Label>{item.label}</Label>
                        </Dropdown.Item>
                    ))}
                </Dropdown.Menu>
            </Dropdown.Popover>
        </Dropdown>
    );
}

function menuIcon(icon: GridMenuItem["icon"]): ReactNode {
    if (icon === "hide") return <Minus aria-hidden="true" />;
    if (icon === "pin") return <Pin aria-hidden="true" />;
    return <PinSlash aria-hidden="true" />;
}

/**
 * The header cell's actions: hide the unpinned part, pin/unpin the whole target
 * set. A pinned column is never hidden (the same rule the previous static
 * `pinned` descriptors followed), so a mixed group shows both actions.
 */
export function columnMenuItems({
    targets,
    level,
    pinnedKeys,
    service = false,
    onHide,
    onPin,
    onUnpin,
    onHideService,
}: {
    targets: readonly string[];
    level: number;
    pinnedKeys: ReadonlySet<string>;
    service?: boolean;
    onHide: (targets: readonly string[], level: number) => void;
    onPin: (targets: readonly string[]) => void;
    onUnpin: (targets: readonly string[]) => void;
    onHideService: () => void;
}): GridMenuItem[] {
    if (service) {
        return [{ id: "hide", label: "Скрыть", icon: "hide", onSelect: onHideService }];
    }
    const items: GridMenuItem[] = [];
    const hideable = targets.filter((prop) => !pinnedKeys.has(prop));
    const pinned = targets.filter((prop) => pinnedKeys.has(prop));
    if (hideable.length > 0) {
        items.push({
            id: "hide",
            label: "Скрыть",
            icon: "hide",
            onSelect: () => onHide(hideable, level),
        });
    }
    if (hideable.length > 0) {
        items.push({
            id: "pin",
            label: "Закрепить",
            icon: "pin",
            onSelect: () => onPin(hideable),
        });
    }
    if (pinned.length > 0) {
        items.push({
            id: "unpin",
            label: "Открепить",
            icon: "unpin",
            onSelect: () => onUnpin(pinned),
        });
    }
    return items;
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
    priority = 0,
}: {
    label: string;
    entries: readonly HiddenColumnEntry[];
    onPress: () => void;
    style?: CSSProperties;
    /** Later hide actions sit above earlier ones at an overlapping boundary. */
    priority?: number;
}) {
    return (
        <span
            className="grid-unhide-anchor"
            data-unhide-priority={priority}
            {...(style !== undefined && {
                style: { ...style, zIndex: 10 + priority },
            })}
            {...(style === undefined && { style: { zIndex: 10 + priority } })}
        >
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
