/**
 * Renovation API — undo / redo / undo-state.
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import { requestJson, requestWrite } from "@/shared/api/api-error";
import { getCollabSessionId } from "@/shared/collab/ws-client";

import { RENOVATION_SECTION } from "./renovation-shared";
import type { RenovationUndoResult, RenovationUndoState } from "./renovation-shared";

export async function undoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/undo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function redoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/redo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function getUndoState(
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoState> {
    const params = new URLSearchParams({ section, session_id: getCollabSessionId() });
    return requestJson(`/api/renovation/undo-state?${params.toString()}`);
}
