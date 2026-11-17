"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import type { CurrentUser } from "@/shared/api/report-collector";
import { projectSlugFromKey } from "@/shared/projects";

/**
 * Each project's workspace is loaded ONLY when its URL is open.
 *
 * All three used to be static imports, so every visitor downloaded the grids of
 * all three projects — and a grid is `@revolist/react-datagrid`, the bulk of the
 * 2.1 MB chunk. Opening «Движение» paid for the КППК realization table and the
 * renovation plan as well, none of which can be seen on that page. `ssr: false`
 * because these are client workspaces behind `force-dynamic`: the server renders
 * the shell, and the grid measures its container on mount anyway.
 */
const RawMaterialsWorkspace = dynamic(
    () =>
        import("@/features/raw-materials/raw-materials-workspace").then(
            (m) => m.RawMaterialsWorkspace,
        ),
    { ssr: false },
);
const KppkWorkspace = dynamic(
    () => import("@/features/kppk/kppk-workspace").then((m) => m.KppkWorkspace),
    { ssr: false },
);
const RenovationWorkspace = dynamic(
    () => import("@/features/renovation/renovation-workspace").then((m) => m.RenovationWorkspace),
    { ssr: false },
);
const LogisticsWorkspace = dynamic(
    () => import("@/features/logistics/logistics-workspace").then((m) => m.LogisticsWorkspace),
    { ssr: false },
);

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
    if (activeProjectKey === "logistics") {
        return <LogisticsWorkspace {...sharedProps} />;
    }
    return <RawMaterialsWorkspace {...sharedProps} />;
}
