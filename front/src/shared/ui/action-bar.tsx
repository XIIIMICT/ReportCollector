"use client";

import { ButtonGroup, Dropdown, Kbd, Label, Spinner } from "@heroui/react";
import { MoreHoriz } from "iconoir-react";
import { useCallback, useRef, useState, type Key, type ReactNode } from "react";

import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { ActionHint, parseShortcut } from "@/shared/ui/action-hint";
import { ExpandableButton } from "@/shared/ui/expandable-button";
import { HoverDropdown } from "@/shared/ui/hover-dropdown";

export type ActionItem = {
    id: string;
    label: string;
    icon: ReactNode;
    onPress: () => void;
    variant?: "primary" | "secondary" | "danger";
    disabled?: boolean;
    shortcut?: { key: "Z" | "Y" | "C" | "V" | "Del"; aria: string };
    /**
     * Consecutive items sharing a group render joined in one HeroUI
     * ButtonGroup («Отменить/Вернуть», «Копировать/Вставить»).
     */
    group?: "history" | "clipboard" | "row" | "download";
    /**
     * A trigger with a dropdown (e.g. download with «всю таблицу» /
     * «видимую таблицу» sub-actions) instead of a one-shot button.
     * `icon` renders only in the compact «Действия» menu — the desktop
     * dropdown keeps the plain labels (the trigger already shows its icon).
     */
    menu?: Array<{ id: string; label: string; icon?: ReactNode; onPress: () => void }>;
    /** Show a spinner in place of the icon while a background job runs. */
    pending?: boolean;
    /**
     * Why a disabled action can't run. Rendered as a native tooltip on the
     * button, so a protective block («нельзя удалить себя») explains itself
     * instead of leaving the user guessing.
     */
    disabledReason?: string | null;
};

/** Chunk actions into ButtonGroup runs: consecutive items with equal `group`. */
export function groupActionItems(items: ActionItem[]): ActionItem[][] {
    const chunks: ActionItem[][] = [];
    for (const item of items) {
        const last = chunks[chunks.length - 1];
        const lastFirst = last?.[0];
        if (last && lastFirst && item.group !== undefined && lastFirst.group === item.group) {
            last.push(item);
        } else {
            chunks.push([item]);
        }
    }
    return chunks;
}

/**
 * The desktop action bar: the caller builds the ordered `ActionItem` list, this
 * renders it with the responsive tiers (everything collapses into one
 * «Действия» menu on sm; a primary action collapses to an icon on md) and the
 * floating shortcut hint. Shared by both workspaces.
 */
export function ActionBar({
    items,
    onDropdownOpenChange,
}: {
    items: ActionItem[];
    /** Holds the fullscreen panels open while an action menu is expanded. */
    onDropdownOpenChange?: ((open: boolean) => void) | undefined;
}) {
    const isSm = useMediaQuery("(max-width: 40rem)");
    const isMd = useMediaQuery("(max-width: 48rem)");
    const collapsePrimaryAtMd = isMd && !isSm;
    const isExpanded = (action: ActionItem) => action.variant === "primary" && !collapsePrimaryAtMd;
    // Floating hint: shows the label plus a Kbd with the shortcut over every
    // inline action button, including the ones grouped in a ButtonGroup (whose
    // children can't be wrapped in a per-button Tooltip).
    const [actionTip, setActionTip] = useState<{
        label: string;
        keyLabel: string;
        modifiers: Array<"ctrl" | "shift" | "alt" | "meta">;
        left: number;
        top: number;
    } | null>(null);
    const actionTipTargetRef = useRef<Element | null>(null);

    // The floating hint carries the label plus a Kbd with the shortcut, so it
    // only makes sense for actions that have a shortcut (a plain button like
    // «Добавить строку» shows nothing). A disabled action with a reason shows
    // the reason instead of the shortcut — that is what the user needs then.
    const hintData = (action: ActionItem) => {
        if (action.disabled && action.disabledReason) {
            return { "data-action-tooltip": action.disabledReason };
        }
        return action.shortcut
            ? { "data-action-tooltip": action.label, "data-action-shortcut": action.shortcut.aria }
            : {};
    };

    const renderAction = (action: ActionItem) => {
        const icon = action.pending ? <Spinner size="sm" /> : action.icon;
        if (action.menu) {
            return (
                <HoverDropdown
                    className="action-more-popover"
                    {...(onDropdownOpenChange && { onOpenChange: onDropdownOpenChange })}
                    trigger={({ ref, onMouseEnter, isOpen, onPress }) => (
                        <ExpandableButton
                            ref={ref}
                            onMouseEnter={onMouseEnter}
                            onPress={onPress}
                            variant={action.variant ?? "tertiary"}
                            {...(action.disabled !== undefined && { isDisabled: action.disabled })}
                            aria-label={action.label}
                            {...hintData(action)}
                            icon={icon}
                            isExpanded={isOpen || isExpanded(action)}
                        >
                            {action.label}
                        </ExpandableButton>
                    )}
                    content={
                        <Dropdown.Menu
                            aria-label={action.label}
                            onAction={(key) => {
                                const menuItem = action.menu?.find((m) => m.id === String(key));
                                menuItem?.onPress();
                            }}
                            className="action-more-menu"
                        >
                            {action.menu.map((menuItem) => (
                                <Dropdown.Item
                                    key={menuItem.id}
                                    id={menuItem.id}
                                    textValue={menuItem.label}
                                >
                                    <Label>{menuItem.label}</Label>
                                </Dropdown.Item>
                            ))}
                        </Dropdown.Menu>
                    }
                />
            );
        }
        return (
            <ExpandableButton
                isExpanded={isExpanded(action)}
                variant={action.variant ?? "tertiary"}
                {...(action.disabled !== undefined && { isDisabled: action.disabled })}
                aria-label={action.label}
                aria-keyshortcuts={action.shortcut?.aria}
                {...hintData(action)}
                onPress={action.onPress}
                icon={icon}
            >
                {action.label}
            </ExpandableButton>
        );
    };

    if (isSm) {
        return (
            <div className="action-bar action-bar--compact" aria-label="Действия с таблицей">
                <CompactActionMenu
                    items={items}
                    {...(onDropdownOpenChange && { onOpenChange: onDropdownOpenChange })}
                />
            </div>
        );
    }
    return (
        <div
            className="action-bar"
            aria-label="Действия с таблицей"
            onPointerMove={(event) => {
                const target = (event.target as Element).closest?.("[data-action-tooltip]") ?? null;
                if (target === actionTipTargetRef.current) return;
                actionTipTargetRef.current = target;
                if (!target) {
                    setActionTip(null);
                    return;
                }
                const rect = target.getBoundingClientRect();
                const shortcut = target.getAttribute("data-action-shortcut");
                const parsed = shortcut ? parseShortcut(shortcut) : null;
                setActionTip({
                    label: target.getAttribute("data-action-tooltip") ?? "",
                    keyLabel: parsed?.keyLabel ?? "",
                    modifiers: parsed?.modifiers ?? [],
                    left: rect.left + rect.width / 2,
                    top: rect.top,
                });
            }}
            onPointerLeave={() => {
                actionTipTargetRef.current = null;
                setActionTip(null);
            }}
        >
            {groupActionItems(items).map((chunk) => {
                const firstAction = chunk[0];
                if (!firstAction) return null;
                return chunk.length > 1 ? (
                    // ExpandableButton forwards HeroUI's injected group props
                    // to its Button, with no intervening DOM wrapper.
                    <ButtonGroup key={`group-${firstAction.id}`}>
                        {chunk.map((action) => (
                            <ExpandableButton
                                key={action.id}
                                isExpanded={isExpanded(action)}
                                icon={action.pending ? <Spinner size="sm" /> : action.icon}
                                variant={action.variant ?? "tertiary"}
                                {...(action.disabled !== undefined && {
                                    isDisabled: action.disabled,
                                })}
                                aria-label={action.label}
                                aria-keyshortcuts={action.shortcut?.aria}
                                {...hintData(action)}
                                onPress={action.onPress}
                            >
                                {action.label}
                            </ExpandableButton>
                        ))}
                    </ButtonGroup>
                ) : (
                    <span
                        key={firstAction.id}
                        className="action-button-wrapper"
                        {...(firstAction.disabled && firstAction.disabledReason
                            ? { "data-action-tooltip": firstAction.disabledReason }
                            : {})}
                    >
                        {renderAction(firstAction)}
                    </span>
                );
            })}
            {actionTip !== null ? (
                <ActionHint
                    left={actionTip.left}
                    top={actionTip.top}
                    label={actionTip.label}
                    keyLabel={actionTip.keyLabel}
                    modifiers={actionTip.modifiers}
                />
            ) : null}
        </div>
    );
}

/**
 * The compact action menu (narrow screens): a single «Действия» button whose
 * dropdown (full-text items with icons — the same actions as the inline bar)
 * opens on hover. See `HoverDropdown` for why closing is pointer-position
 * based rather than mouseleave.
 */
export function CompactActionMenu({
    items,
    onOpenChange,
}: {
    items: ActionItem[];
    /** Notifies the owner when the menu opens/closes (holds the fullscreen
        panels open while the compact «Действия» menu is expanded). */
    onOpenChange?: (open: boolean) => void;
}) {
    const handleAction = useCallback(
        (key: Key) => {
            const raw = String(key);
            for (const item of items) {
                if (item.menu) {
                    const sub = item.menu.find((m) => `${item.id}:${m.id}` === raw);
                    if (sub) {
                        sub.onPress();
                        return;
                    }
                } else if (item.id === raw) {
                    item.onPress();
                    return;
                }
            }
        },
        [items],
    );

    return (
        <HoverDropdown
            className="action-more-popover"
            {...(onOpenChange && { onOpenChange })}
            trigger={({ ref, onMouseEnter, isOpen, onPress }) => (
                <ExpandableButton
                    ref={ref}
                    onMouseEnter={onMouseEnter}
                    onPress={onPress}
                    isExpanded={isOpen}
                    icon={<MoreHoriz aria-hidden="true" />}
                    variant="tertiary"
                    aria-label="Действия с таблицей"
                >
                    Действия
                </ExpandableButton>
            )}
            content={
                <Dropdown.Menu
                    aria-label="Действия с таблицей"
                    onAction={handleAction}
                    className="action-more-menu"
                >
                    {items.flatMap((item) =>
                        item.menu
                            ? item.menu.map((sub) => (
                                  <Dropdown.Item
                                      key={`${item.id}:${sub.id}`}
                                      id={`${item.id}:${sub.id}`}
                                      textValue={sub.label}
                                  >
                                      {sub.icon ? (
                                          <span className="action-more-menu__icon">{sub.icon}</span>
                                      ) : null}
                                      <Label>{sub.label}</Label>
                                  </Dropdown.Item>
                              ))
                            : [
                                  <Dropdown.Item
                                      key={item.id}
                                      id={item.id}
                                      textValue={item.label}
                                      {...(item.disabled !== undefined && {
                                          isDisabled: item.disabled,
                                      })}
                                      {...(item.variant === "danger" && { variant: "danger" })}
                                      // HeroUI ships only a `danger` MenuItem variant; success is
                                      // reproduced the same way — color the label slot via the
                                      // theme's `--success` token (stock Tailwind utility, no CSS).
                                      {...(item.variant === "primary" && {
                                          className: "[&_[data-slot=label]]:text-success",
                                      })}
                                  >
                                      <span className="action-more-menu__icon">{item.icon}</span>
                                      <Label>{item.label}</Label>
                                      {item.shortcut ? (
                                          <Kbd className="ms-auto" slot="keyboard" variant="light">
                                              <Kbd.Abbr keyValue="ctrl" />
                                              <Kbd.Content>{item.shortcut.key}</Kbd.Content>
                                          </Kbd>
                                      ) : null}
                                  </Dropdown.Item>,
                              ],
                    )}
                </Dropdown.Menu>
            }
        />
    );
}
