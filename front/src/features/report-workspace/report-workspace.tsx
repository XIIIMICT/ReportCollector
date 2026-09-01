"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { RawMaterialsWorkspace } from "@/features/raw-materials/raw-materials-workspace";
import { RenovationWorkspace } from "@/features/renovation/renovation-workspace";
import type { CurrentUser } from "@/shared/api/report-collector";
import { projectSlugFromKey } from "@/shared/projects";

export type ReportWorkspaceUser = CurrentUser;

type ReportWorkspaceProps = {
    currentUser: CurrentUser;
    /** The project pinned by the URL (/renovation, /raw-materials, /kppk). */
    projectKey: string;
};

export function ReportWorkspace({ currentUser, projectKey }: ReportWorkspaceProps) {
    const router = useRouter();
    const [activeProjectKey, setActiveProjectKey] = useState(projectKey);

    // The URL owns the active project (deep links, browser navigation).
    useEffect(() => {
        setActiveProjectKey(projectKey);
    }, [projectKey]);

    if (!currentUser) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    const handleProjectChange = (key: string) => {
        const slug = projectSlugFromKey(key);
        if (slug !== null) {
            router.push(slug);
        }
    };

    const sharedProps = {
        currentUser,
        activeProjectKey,
        onProjectChange: handleProjectChange,
    };

    return activeProjectKey === "renovation" ? (
        <RenovationWorkspace {...sharedProps} />
    ) : (
        <RawMaterialsWorkspace {...sharedProps} />
    );
}
