"use client";

import { useEffect } from "react";

import { RawMaterialsWorkspace } from "@/features/raw-materials/raw-materials-workspace";
import type { CurrentUser } from "@/shared/api/report-collector";

export type ReportWorkspaceUser = CurrentUser;

type ReportWorkspaceProps = {
    currentUser?: CurrentUser;
};

export function ReportWorkspace({ currentUser }: ReportWorkspaceProps) {
    useEffect(() => {
        if (!currentUser) {
            window.location.replace("/api/auth/login");
        }
    }, [currentUser]);

    if (!currentUser) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    return <RawMaterialsWorkspace currentUser={currentUser} />;
}
