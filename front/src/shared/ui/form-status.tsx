"use client";

import { Tooltip } from "@heroui/react";

import {
    COLLAB_STATUS_LABELS,
    memberDetail,
    memberSelectionLabel,
    membershipSummary,
} from "@/shared/collab/collab-presence";
import type { CollabRoom } from "@/shared/collab/use-collab-room";
import type { CollabMember } from "@/shared/collab/ws-client";
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";

/**
 * One merged footer summary for a collaborating table: the collab presence
 * («На форме» — who else is on the report at the moment) and the selection /
 * filter breakdown («Выделено», «Строки», «Столбцы»).
 *
 * Inline (the trigger) it reads top-to-bottom «На форме» → «Выделено» with the
 * status badge centered between them; the hover tooltip spells out the
 * member presence list first, then the selection/statistics breakdown. Presence is
 * optional — pass `room={null}` and only the selection summary renders.
 */
export function FormStatus({
    room,
    selectionLabel,
    selectionDetail,
    gridStats,
}: {
    room: CollabRoom | null;
    selectionLabel: string | null;
    /**
     * The selection spelled out in full for the tooltip — the renovation plan
     * passes the header chain («1) Техническая дирекция → … → Всего, строка 3»);
     * falls back to `selectionLabel`.
     */
    selectionDetail?: string | null;
    gridStats: GridViewStats | null;
}) {
    const hasPresence = room !== null && room.status === "online";
    const members = room?.members ?? [];
    const selfId = room?.me?.userId ?? null;
    // The tooltip list: other members first (with what they have selected), then
    // the current user's own name last WITHOUT a selection — the standalone
    // «Выделено» block that follows covers it.
    const others = members
        .filter((member) => member.userId !== selfId)
        .sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email, "ru"));
    const me: CollabMember | null =
        members.find((member) => member.userId === selfId) ?? room?.me ?? null;
    const hasOthers = others.length > 0;
    const presenceText = hasPresence
        ? membershipSummary(members, selfId)
        : room && room.status !== "online"
          ? COLLAB_STATUS_LABELS[room.status]
          : null;

    return (
        <Tooltip delay={250}>
            <Tooltip.Trigger>
                <div className="form-status" aria-label="Статус таблицы">
                    {room ? (
                        <span
                            className={`form-status__dot form-status__dot--${room.status}`}
                            aria-hidden="true"
                        />
                    ) : null}
                    <div className="form-status__body">
                        {presenceText !== null ? (
                            <div
                                className={
                                    hasPresence
                                        ? "form-status__presence"
                                        : "form-status__presence form-status__presence--bold"
                                }
                            >
                                {hasPresence ? (
                                    <>
                                        <b>На форме:</b> {presenceText}
                                    </>
                                ) : (
                                    // No live collaboration: the state itself
                                    // («Совместное редактирование отключено»,
                                    // «Подключение…») stands alone, bold, with
                                    // no «На форме» prefix.
                                    <b>{presenceText}</b>
                                )}
                            </div>
                        ) : null}
                        {selectionLabel ? (
                            <div className="form-status__selection">
                                <b>Выделено:</b> {selectionLabel}
                            </div>
                        ) : null}
                        {gridStats ? (
                            <div className="form-status__rows">
                                <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                {gridStats.totalRows} всего
                            </div>
                        ) : null}
                    </div>
                </div>
            </Tooltip.Trigger>
            <Tooltip.Content placement="top" className="form-status-tooltip">
                <div className="form-status-detail">
                    {hasPresence ? (
                        <div className="form-status-detail__presence">
                            <div className="form-status-detail__presence-summary">
                                {hasOthers || me ? (
                                    <b>На форме:</b>
                                ) : (
                                    <>
                                        <b>На форме:</b> только вы
                                    </>
                                )}
                            </div>
                            {hasOthers || me ? (
                                <ul className="form-status-detail__presence-list">
                                    {others.map((member) => (
                                        <PresenceRow member={member} key={member.userId} />
                                    ))}
                                    {me ? (
                                        <li className="form-status-detail__presence-item">
                                            <span className="form-status-detail__head">
                                                <span className="form-status-detail__name">
                                                    {me.name || me.email}
                                                </span>
                                                {memberDetail(me) ? (
                                                    <span className="form-status-detail__role">
                                                        ({memberDetail(me)})
                                                    </span>
                                                ) : null}
                                            </span>
                                        </li>
                                    ) : null}
                                </ul>
                            ) : null}
                        </div>
                    ) : null}
                    {selectionLabel ? (
                        <div className="form-status-detail__row">
                            <span>
                                <b>Выделено:</b> {selectionDetail ?? selectionLabel}
                            </span>
                        </div>
                    ) : null}
                    {gridStats ? (
                        <div className="form-status-detail__stats">
                            <span className="form-status-detail__rows">
                                <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                {gridStats.totalRows} всего
                            </span>
                            <span className="form-status-detail__cols">
                                <b>Столбцы:</b> {gridStats.immutableCols} неизменяемых +{" "}
                                {gridStats.calculatedCols} расчётных + {gridStats.editableCols}{" "}
                                заполняемых = {gridStats.totalCols} всего
                            </span>
                        </div>
                    ) : null}
                </div>
            </Tooltip.Content>
        </Tooltip>
    );
}

function PresenceRow({ member }: { member: CollabMember }) {
    const cells = memberSelectionLabel(member);
    const detail = memberDetail(member);
    return (
        <li className="form-status-detail__presence-item">
            <span className="form-status-detail__head">
                <span className="form-status-detail__name">{member.name || member.email}</span>
                {detail ? <span className="form-status-detail__role">({detail})</span> : null}
            </span>
            {cells ? <span className="form-status-detail__cells">{cells}</span> : null}
        </li>
    );
}
