import { toast } from "@heroui/react";

import { errorMessage } from "./text-helpers";

export type HistoryDirection = "undo" | "redo";
export type PendingHistorySkip = { actionId: number; direction: HistoryDirection };
type HistoryResult = {
    applied: boolean;
    reason: string | null;
    conflicts: { actionId: number; reason: string; field: string; user: string }[];
};

/** Interpret a replay without owning any React state or form/session scope. */
export function applyHistoryResult({
    result,
    direction,
    setPendingSkip,
    refreshState,
    refresh,
    businessRuleFields = [],
}: {
    result: HistoryResult;
    direction: HistoryDirection;
    setPendingSkip: (next: PendingHistorySkip | null) => void;
    refreshState: () => void;
    refresh: () => void;
    businessRuleFields?: readonly string[];
}): void {
    if (result.applied || result.reason === "empty") {
        setPendingSkip(null);
        refreshState();
        if (result.applied) {
            refresh();
            toast.success(direction === "undo" ? "Действие отменено" : "Действие возвращено");
        }
        return;
    }
    const conflict = result.conflicts[0];
    if (!conflict) {
        setPendingSkip(null);
        return;
    }
    setPendingSkip({ actionId: conflict.actionId, direction });
    const owner = conflict.user || "другой пользователь";
    const description =
        conflict.reason === "locked"
            ? `Ячейку сейчас редактирует ${owner}. Нажмите ещё раз, чтобы пропустить действие.`
            : businessRuleFields.includes(conflict.field)
              ? `${owner} Нажмите ещё раз, чтобы пропустить действие.`
              : `Данные изменил ${owner}. Нажмите ещё раз, чтобы пропустить это действие.`;
    toast.warning("Действие не выполнено", { description });
}

/** Wait for the current write, then replay/skip in the caller's API scope. */
export async function runHistoryAction<TResult>({
    direction,
    pendingWrite,
    pendingSkip,
    request,
    apply,
}: {
    direction: HistoryDirection;
    pendingWrite: Promise<unknown> | null;
    pendingSkip: PendingHistorySkip | null;
    request: (skipActionId: number | null) => Promise<TResult>;
    apply: (result: TResult, direction: HistoryDirection) => void;
}): Promise<void> {
    try {
        await pendingWrite;
        const result = await request(
            pendingSkip?.direction === direction ? pendingSkip.actionId : null,
        );
        apply(result, direction);
    } catch (reason) {
        toast.danger(
            direction === "undo" ? "Не удалось отменить действие" : "Не удалось вернуть действие",
            {
                description: errorMessage(reason),
            },
        );
    }
}
