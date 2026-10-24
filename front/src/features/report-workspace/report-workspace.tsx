"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { KppkWorkspace } from "@/features/kppk/kppk-workspace";
import { RawMaterialsWorkspace } from "@/features/raw-materials/raw-materials-workspace";
import { RenovationWorkspace } from "@/features/renovation/renovation-workspace";
import type { CurrentUser } from "@/shared/api/report-collector";
import { projectSlugFromKey } from "@/shared/projects";

export type ReportWorkspaceUser = CurrentUser;

type ReportWorkspaceProps = {
    currentUser: CurrentUser;
    /** The project pinned by the URL (/renovation, /raw-materials, /kppk). */
    projectKey: string;
    /** The form/tab picked last time, restored from `?section=`. */
    initialSection?: string | null;
    /** `SUPPORT_MAIL` — the «Связаться с разработчиком» mailbox ('' hides it). */
    supportMail: string;
};

export function ReportWorkspace({
    currentUser,
    projectKey,
    initialSection = null,
    supportMail,
}: ReportWorkspaceProps) {
    const router = useRouter();
    // The URL owns the active project (deep links, browser navigation), so the
    // prop IS the state — mirroring it into `useState` via an effect cost an
    // extra render and could show the previous project for a frame.
    const activeProjectKey = projectKey;

    // A form change is mirrored into `?section=` so a reload lands on the same
    // form. `replace` (not `push`) keeps the back button on the previous page;
    // scroll is untouched. Other view state (filters, version, …) is not saved.
    const handleSectionChange = useCallback(
        (section: string | null) => {
            const slug = projectSlugFromKey(activeProjectKey);
            if (slug === null) return;
            const params = new URLSearchParams(window.location.search);
            if (section) params.set("section", section);
            else params.delete("section");
            router.replace(`${slug}${params.toString() ? `?${params.toString()}` : ""}`, {
                scroll: false,
            });
        },
        [activeProjectKey, router],
    );

    if (!currentUser) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    const handleProjectChange = (key: string) => {
        const slug = projectSlugFromKey(key);
        if (slug !== null) {
            // Drop `?section=`: form ids differ per project.
            router.push(slug);
        }
    };

    const sharedProps = {
        currentUser,
        activeProjectKey,
        onProjectChange: handleProjectChange,
        initialSection,
        onSectionChange: handleSectionChange,
        supportMail,
    };

    if (activeProjectKey === "renovation") {
        return <RenovationWorkspace {...sharedProps} />;
    }
    if (activeProjectKey === "kppk") {
        return <KppkWorkspace {...sharedProps} />;
    }
    return <RawMaterialsWorkspace {...sharedProps} />;
}
