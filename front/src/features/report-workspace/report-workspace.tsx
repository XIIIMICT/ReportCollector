"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

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
};

export function ReportWorkspace({
    currentUser,
    projectKey,
    initialSection = null,
}: ReportWorkspaceProps) {
    const router = useRouter();
    const [activeProjectKey, setActiveProjectKey] = useState(projectKey);

    // The URL owns the active project (deep links, browser navigation).
    useEffect(() => {
        setActiveProjectKey(projectKey);
    }, [projectKey]);

    // A form change is mirrored into `?section=` so a reload lands on the same
    // form. `replace` (not `push`) keeps the back button on the previous page;
    // scroll is untouched. Other view state (filters, version, …) is not saved.
    const handleSectionChange = useCallback(
        (section: string | null) => {
            const slug = projectSlugFromKey(activeProjectKey);
            if (slug === null) return;
            router.replace(section ? `${slug}?section=${encodeURIComponent(section)}` : slug, {
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
    };

    return activeProjectKey === "renovation" ? (
        <RenovationWorkspace {...sharedProps} />
    ) : (
        <RawMaterialsWorkspace {...sharedProps} />
    );
}
