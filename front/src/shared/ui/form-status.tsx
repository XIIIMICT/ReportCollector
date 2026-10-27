"use client";

import { Tooltip } from "@heroui/react";
import { Fragment, useEffect, useRef } from "react";

import {
    COLLAB_STATUS_LABELS,
    memberDetail,
    memberSelectionLabel,
    membershipSummary,
} from "@/shared/collab/collab-presence";
import type { CollabRoom } from "@/shared/collab/use-collab-room";
import type { CollabMember } from "@/shared/collab/ws-client";
import type { GridViewStats } from "@/shared/grid/data-grid";

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
    // The «Выделено» row is collapsed, not unmounted (see below), so it must
    // keep a value to render while it fades OUT — otherwise the closing frame
    // would read a bare «Выделено:» with nothing after it. Written in an effect,
    // never during render.
    const lastSelectionRef = useRef<string | null>(null);
    useEffect(() => {
        if (selectionLabel !== null) lastSelectionRef.current = selectionLabel;
    }, [selectionLabel]);
    const shownSelection = selectionLabel ?? lastSelectionRef.current;

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
                        {/* Mounted even when empty — BOTH the row and its
                            text: the row animates its own height, and if the
                            text unmounted on clear there would be nothing left
                            to animate out (the line would vanish in one frame
                            while it slides in smoothly). The row clips it. */}
                        <div
                            className={
                                selectionLabel
                                    ? "form-status__selection form-status__selection--shown"
                                    : "form-status__selection"
                            }
                            aria-hidden={selectionLabel ? undefined : true}
                        >
                            <div>
                                <b>Выделено:</b> {shownSelection ?? ""}
                            </div>
                        </div>
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
                    <FilterSection chains={gridStats?.filteredColumns ?? []} />
                    <SortSection entries={gridStats?.sortedColumns ?? []} />
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

/**
 * «Сортировка:» — the columns the table is sorted by, in the order the grid
 * applies them, each as its header CHAIN plus the direction.
 *
 * It sits after «Фильтры:» because it answers the same kind of question — what
 * is currently narrowing or reordering the view — and, like it, renders nothing
 * when the table is unsorted.
 */
export function SortSection({
    entries,
}: {
    entries: Array<{ path: string[]; order: "asc" | "desc" }>;
}) {
    if (entries.length === 0) return null;
    return (
        <>
            <div className="form-status-detail__gap" aria-hidden="true" />
            <div className="form-status-detail__filters">
                <b>Сортировка:</b>
                {/* NUMBERED: RevoGrid sorts by several columns, and the ORDER is
                    part of the answer — «1. Наименование ↑, 2. Код ↓» says which
                    column decides first. A bulleted list would hide that. */}
                <ol className="form-status-detail__filters-list form-status-detail__sort-list">
                    {entries.map((entry) => (
                        <li
                            key={`${entry.path.join(" → ")}:${entry.order}`}
                            className="form-status-detail__filters-item"
                        >
                            {entry.path.map((label, index) => (
                                <Fragment key={`${label}-${index}`}>
                                    {index > 0 ? (
                                        <span
                                            className="form-status-detail__filters-sep"
                                            aria-hidden="true"
                                        >
                                            {" → "}
                                        </span>
                                    ) : null}
                                    <span>{label}</span>
                                </Fragment>
                            ))}
                            <span className="form-status-detail__sort-order">
                                {entry.order === "asc" ? " ↑" : " ↓"}
                            </span>
                        </li>
                    ))}
                </ol>
            </div>
        </>
    );
}

/**
 * «Фильтры:» — the applied column filters, one bullet per column, each spelled
 * out as its full header CHAIN (group first, leaf last: «Иерархия → Лист»), the
 * way the headers stack on screen. Preceded by one empty line so it reads as its
 * own block.
 *
 * Nothing is rendered when no filter is applied — an empty section would claim
 * the table has a filter feature it is not using.
 */
export function FilterSection({ chains }: { chains: string[][] }) {
    if (chains.length === 0) return null;
    return (
        <>
            <div className="form-status-detail__gap" aria-hidden="true" />
            <div className="form-status-detail__filters">
                <b>Фильтры:</b>
                <ul className="form-status-detail__filters-list">
                    {chains.map((path) => (
                        <li key={path.join(" → ")} className="form-status-detail__filters-item">
                            {path.map((label, index) => (
                                <Fragment key={`${label}-${index}`}>
                                    {index > 0 ? (
                                        <span
                                            className="form-status-detail__filters-sep"
                                            aria-hidden="true"
                                        >
                                            {" → "}
                                        </span>
                                    ) : null}
                                    <span>{label}</span>
                                </Fragment>
                            ))}
                        </li>
                    ))}
                </ul>
            </div>
        </>
    );
}
