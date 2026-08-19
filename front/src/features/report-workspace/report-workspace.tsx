"use client";

import { useEffect, useState } from "react";

import { RawMaterialsWorkspace } from "@/features/raw-materials/raw-materials-workspace";
import { RenovationWorkspace } from "@/features/renovation/renovation-workspace";
import type { CurrentUser } from "@/shared/api/report-collector";

export type ReportWorkspaceUser = CurrentUser;

type ReportWorkspaceProps = {
    currentUser?: CurrentUser;
};

const RENOVATION_PROJECT_KEY = "renovation";

export function ReportWorkspace({ currentUser }: ReportWorkspaceProps) {
    const [activeProjectKey, setActiveProjectKey] = useState<string | null>(null);

    useEffect(() => {
        if (!currentUser) {
            window.location.replace("/api/auth/login");
            return;
        }
        // The first project in the access list opens by default.
        const firstKey = currentUser.access?.[0]?.key ?? null;
        setActiveProjectKey((current) => current ?? firstKey);
    }, [currentUser]);

    if (!currentUser || activeProjectKey === null) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    return activeProjectKey === RENOVATION_PROJECT_KEY ? (
        <RenovationWorkspace
            currentUser={currentUser}
            activeProjectKey={activeProjectKey}
            onProjectChange={setActiveProjectKey}
        />
    ) : (
        <RawMaterialsWorkspace
            currentUser={currentUser}
            activeProjectKey={activeProjectKey}
            onProjectChange={setActiveProjectKey}
        />
    );
}
