"use client";

import { Button, Dropdown } from "@heroui/react";
import { useMemo } from "react";

import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/**
 * The brand in the app header.
 *
 * It lives in its own module because a SERVER page renders it too (the «no
 * access» screen of `[project]/page.tsx`). Importing it from the raw-materials
 * workspace — a much larger client entry — made Next hand the page a CLIENT
 * REFERENCE, and the page then failed with «Event handlers cannot be passed to
 * Client Component props»: `onProjectChange` arrived as a reference, not a
 * function, and the whole project page became a 500.
 *
 * With one project there is nothing to choose, so the edition is plain text and
 * the handler is never used.
 */
export function Brand({
    compact = false,
    projectName,
    projects,
    activeProjectKey,
    onProjectChange,
}: {
    compact?: boolean;
    projectName: string;
    projects: Array<{ key: string; name: string }>;
    activeProjectKey: string;
    /**
     * How to open another project. OPTIONAL on purpose: a Server Component
     * cannot hand a function to a client one at all, and the «no access» screen
     * of `[project]/page.tsx` is a server component with nothing to switch to —
     * it renders the brand as plain text.
     */
    onProjectChange?: (key: string) => void;
}) {
    // Stable identity: an inline `new Set(...)` changes on every render and
    // loops HeroUI's selection sync ("Maximum update depth exceeded"). Declared
    // before the early return below: hooks must run unconditionally.
    const selectedProjectKeys = useMemo(() => new Set([activeProjectKey]), [activeProjectKey]);

    if (projects.length <= 1 || !onProjectChange) {
        return (
            <div
                className={compact ? "brand brand--compact" : "brand"}
                aria-label={`РепортКоллектор ${projectName}`}
            >
                <img
                    className="brand__icon"
                    src="/ruschem_icon.png"
                    alt=""
                    onError={(event) => {
                        event.currentTarget.hidden = true;
                    }}
                />
                <span className="brand__name">РепортКоллектор</span>
                <span className="brand__edition">{projectName}</span>
            </div>
        );
    }

    return (
        <div className={compact ? "brand brand--compact" : "brand"}>
            <img
                className="brand__icon"
                src="/ruschem_icon.png"
                alt=""
                onError={(event) => {
                    event.currentTarget.hidden = true;
                }}
            />
            <span className="brand__name">РепортКоллектор</span>
            <HoverDropdown
                className="brand-popover"
                trigger={({ ref, onHoverStart, onPress }) => (
                    <Button
                        ref={ref}
                        onHoverStart={onHoverStart}
                        onPress={onPress}
                        className="brand__edition brand__edition--button"
                        variant="ghost"
                        aria-label={`Проект: ${projectName}. Выберите проект`}
                    >
                        {projectName}
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        aria-label="Доступные проекты"
                        selectedKeys={selectedProjectKeys}
                        selectionMode="single"
                        onSelectionChange={(selection) => {
                            const keys = selection === "all" ? [] : [...selection];
                            if (keys[0]) onProjectChange?.(String(keys[0]));
                        }}
                    >
                        {projects.map((project) => (
                            <Dropdown.Item
                                key={project.key}
                                id={project.key}
                                textValue={project.name}
                            >
                                <Dropdown.ItemIndicator />
                                {project.name}
                            </Dropdown.Item>
                        ))}
                    </Dropdown.Menu>
                }
            />
        </div>
    );
}
