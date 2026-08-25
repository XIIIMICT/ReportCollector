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
 * selection breakdown first, then the member presence list. Presence is
 * optional — pass `room={null}` and only the selection summary renders.
 */
export function FormStatus({
    room,
    selectionLabel,
    gridStats,
}: {
    room: CollabRoom | null;
    selectionLabel: string | null;
    gridStats: GridViewStats | null;
}) {
    const hasPresence = room !== null && room.status === "online";
    const members = room?.members ?? [];
    const selfId = room?.me?.userId ?? null;
    const presenceText = hasPresence
        ? membershipSummary(members, selfId)
        : room && room.status !== "online"
          ? COLLAB_STATUS_LABELS[room.status]
          : null;
    const solo = members.length <= 1;

    const showInline =
        presenceText !== null || selectionLabel !== null || gridStats?.filtersApplied;

    return (
        <Tooltip delay={250}>
            <Tooltip.Trigger>
                <div className="form-status" aria-label="Статус таблицы">
                    {showInline ? (
                        <>
                            {presenceText !== null ? (
                                <div className="form-status__presence">
                                    <b>На форме:</b> {presenceText}
                                </div>
                            ) : null}
                            {room ? (
                                <div className="form-status__badge" aria-hidden="true">
                                    <span
                                        className={`form-status__dot form-status__dot--${room.status}`}
                                    />
                                </div>
                            ) : null}
                            {selectionLabel ? (
                                <div className="form-status__selection">
                                    <b>Выделено:</b> {selectionLabel}
                                </div>
                            ) : null}
                            {gridStats?.filtersApplied ? (
                                <div className="form-status__rows">
                                    <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                    {gridStats.totalRows} всего
                                </div>
                            ) : null}
                        </>
                    ) : null}
                </div>
            </Tooltip.Trigger>
            <Tooltip.Content placement="top" className="form-status-tooltip">
                <div className="form-status-detail">
                    {selectionLabel ? (
                        <div className="form-status-detail__row">
                            <span>
                                <b>Выделено:</b> {selectionLabel}
                            </span>
                        </div>
                    ) : null}
                    {gridStats ? (
                        <>
                            <span>
                                <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                {gridStats.totalRows} всего
                            </span>
                            <span>
                                <b>Столбцы:</b> {gridStats.immutableCols} неизменяемых +{" "}
                                {gridStats.calculatedCols} расчётных + {gridStats.editableCols}{" "}
                                заполняемых = {gridStats.totalCols} всего
                            </span>
                        </>
                    ) : null}
                    {hasPresence && !solo ? (
                        <ul className="form-status-detail__presence">
                            {members.map((member) => (
                                <PresenceRow member={member} key={member.userId} />
                            ))}
                        </ul>
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
