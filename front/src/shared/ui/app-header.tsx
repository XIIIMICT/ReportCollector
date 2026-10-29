"use client";

import { Avatar, Button } from "@heroui/react";

import type { CurrentUser } from "@/shared/api/report-collector";

import { Brand } from "./brand";
import { SupportButton } from "./support-button";

export type AppHeaderProps = {
    user: CurrentUser;
    projects: Array<{ key: string; name: string }>;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
    onOpenProfile: () => void;
    /** ``SUPPORT_MAIL`` — the «Связаться с разработчиком» mailbox ('' hides it). */
    supportMail: string;
};

/** Shared application chrome used by every project workspace. */
export function AppHeader({
    user,
    projects,
    activeProjectKey,
    onProjectChange,
    onOpenProfile,
    supportMail,
}: AppHeaderProps) {
    const fullname = user.name ?? "";
    const email = user.email ?? "";
    const initials = user.name
        ? user.name
              .trim()
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 2)
              .map((part) => part[0])
              .join("")
              .toUpperCase()
        : (email[0] ?? "").toUpperCase();
    const projectName =
        projects.find((project) => project.key === activeProjectKey)?.name ?? "Сырьё";

    return (
        <header className="app-header">
            <Brand
                compact
                projectName={projectName}
                projects={projects}
                activeProjectKey={activeProjectKey}
                onProjectChange={onProjectChange}
            />
            <span aria-hidden="true" />
            <div className="app-header__actions">
                <Button
                    className="profile-pill"
                    variant="ghost"
                    onPress={onOpenProfile}
                    aria-label="Открыть профиль"
                >
                    <Avatar className="profile-pill__avatar" aria-hidden="true">
                        <Avatar.Fallback>{initials || "?"}</Avatar.Fallback>
                    </Avatar>
                    <span className="profile-pill__copy">
                        <strong>{fullname || email}</strong>
                        <small>{email}</small>
                    </span>
                </Button>
                <SupportButton projectName={projectName} supportMail={supportMail} />
            </div>
        </header>
    );
}
