"""«Логистика» API: read-only dictionaries backed by the external `dict` schema."""

from collections.abc import Sequence
from datetime import date
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.collab.events import publish_cell_changed, publish_rows_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.persistence import JournalStore
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.common.export import ExportValidationError
from src.database import get_session
from src.downloads import XLSX_MEDIA_TYPE

from .constants import LOGISTICS_PROJECT_KEY, LOGISTICS_SECTION_RATES, LOGISTICS_SECTION_USERS
from .core import LogisticsNotFoundError, LogisticsSourceUnavailableError, LogisticsValidationError
from .dictionaries import read_countries, read_hazard_classes, read_orgs, read_transport_types
from .export import render_workbook, validate_request
from .models import LogisticsRate
from .rates import (
    create_rate,
    delete_rate,
    rate_snapshot,
    read_rate_row,
    read_rate_rows,
    read_rate_view,
    update_rate_cell,
)
from .schemas import (
    LogisticsCountryRowRead,
    LogisticsDictionaryRead,
    LogisticsExportViewRequest,
    LogisticsHazardClassRowRead,
    LogisticsOrgRowRead,
    LogisticsRateCellUpdate,
    LogisticsRateCreateRequest,
    LogisticsRateRowRead,
    LogisticsRateViewRead,
    LogisticsTransportRowRead,
    LogisticsUserCreateRequest,
    LogisticsUserRowRead,
    LogisticsUserUpdateRequest,
)
from .service import LogisticsMembership, NoLogisticsAccessError, resolve_logistics_access
from .users import (
    create_logistics_user,
    delete_logistics_user,
    read_logistics_user_rows,
    update_logistics_user,
)

router = APIRouter(prefix="/logistics", tags=["logistics"])

SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
IdentityDependency = Annotated[CurrentUserIdentity, Depends(read_signed_identity)]


class LogisticsAccess:
    """Resolved access of the current user to the «Логистика» project."""

    def __init__(self, *, membership: LogisticsMembership) -> None:
        self.user = membership.user
        self.role = membership.role
        self.asset_ids = membership.asset_ids
        self.is_admin = membership.is_admin


async def _resolve_access(session: AsyncSession, identity: CurrentUserIdentity) -> LogisticsAccess:
    try:
        membership = await resolve_logistics_access(session, identity)
    except NoLogisticsAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Логистика».",
        ) from error
    return LogisticsAccess(membership=membership)


async def require_logistics_access(
    session: SessionDependency,
    identity: IdentityDependency,
) -> LogisticsAccess:
    """Any non-blocked member: admin, reviewer or an asset worker with grants."""
    return await _resolve_access(session, identity)


async def require_logistics_admin(
    session: SessionDependency,
    identity: IdentityDependency,
) -> LogisticsAccess:
    """Require the project administrator (the users dictionary, deletes)."""
    access = await _resolve_access(session, identity)
    if not access.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «Логистика».",
        )
    return access


async def require_logistics_editor(
    session: SessionDependency,
    identity: IdentityDependency,
) -> LogisticsAccess:
    """Whoever may write rates: the administrator or an asset worker."""
    access = await _resolve_access(session, identity)
    if not (access.is_admin or access.role == ProjectRole.ASSET_WORKER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Редактировать ставки может администратор или работник на активе.",
        )
    return access


AdminDependency = Annotated[LogisticsAccess, Depends(require_logistics_admin)]
AccessDependency = Annotated[LogisticsAccess, Depends(require_logistics_access)]
EditorDependency = Annotated[LogisticsAccess, Depends(require_logistics_editor)]


@router.get("/dictionaries/countries", response_model=LogisticsDictionaryRead[LogisticsCountryRowRead])
async def get_countries(
    session: SessionDependency,
    _access: AccessDependency,
) -> LogisticsDictionaryRead[LogisticsCountryRowRead]:
    """Country short names from the external dictionary (demo values if absent)."""
    return await read_countries(session)


@router.get(
    "/dictionaries/transport-types",
    response_model=LogisticsDictionaryRead[LogisticsTransportRowRead],
)
async def get_transport_types(
    session: SessionDependency,
    _access: AccessDependency,
) -> LogisticsDictionaryRead[LogisticsTransportRowRead]:
    """Transport types with their vehicle types (demo values if the tables are absent)."""
    return await read_transport_types(session)


@router.get("/dictionaries/orgs", response_model=LogisticsDictionaryRead[LogisticsOrgRowRead])
async def get_orgs(
    session: SessionDependency,
    _access: AccessDependency,
) -> LogisticsDictionaryRead[LogisticsOrgRowRead]:
    """Organization prefixes from the external dictionary (demo values if absent)."""
    return await read_orgs(session)


@router.get("/dictionaries/hazard-classes", response_model=list[LogisticsHazardClassRowRead])
async def get_hazard_classes(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[LogisticsHazardClassRowRead]:
    """Return the «Класс опасности» dictionary (1..9); no tab yet, used by the main table."""
    return await read_hazard_classes(session)


# ─── Project members ───────────────────────────────────────────────────────


@router.get("/users", response_model=list[LogisticsUserRowRead])
async def get_logistics_users(
    session: SessionDependency,
    _access: AdminDependency,
) -> list[LogisticsUserRowRead]:
    """«Пользователи» of the «Логистика» project (admin only)."""
    return await read_logistics_user_rows(session)


# ─── Excel export ─────────────────────────────────────────────────────────


async def _export_rows(
    session: AsyncSession, request: LogisticsExportViewRequest, access: LogisticsAccess
) -> Sequence[BaseModel]:
    """Load the rows of the exported section (the rates are role-scoped)."""
    rows: Sequence[BaseModel]
    if request.section == "logisticsRates":
        month = request.month
        year = request.year
        if month is None or year is None:
            message = "Для выгрузки ставок выберите месяц и год."
            raise ExportValidationError(message)
        scope = access.asset_ids if access.role == ProjectRole.ASSET_WORKER else None
        rows = list(
            await read_rate_rows(
                session,
                period_date=date(year, month, 1),
                asset_ids=scope,
            )
        )
    elif request.section == "logisticsCountries":
        rows = list((await read_countries(session)).rows)
    elif request.section == "logisticsTransportTypes":
        rows = list((await read_transport_types(session)).rows)
    elif request.section == "logisticsOrgs":
        rows = list((await read_orgs(session)).rows)
    else:
        rows = list(await read_logistics_user_rows(session))
    if request.rowIds is not None:
        wanted = set(request.rowIds)
        rows = [row for row in rows if str(getattr(row, "id", "")) in wanted]
    return rows


@router.post("/export")
async def export_section(
    request: LogisticsExportViewRequest,
    session: SessionDependency,
    access: AccessDependency,
) -> Response:
    """Render one «Логистика» grid as XLSX («вся» / «видимая», like every table)."""
    try:
        validate_request(request)
    except ExportValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if request.section == "logisticsUsers" and not access.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Выгружать пользователей может только администратор проекта.",
        )
    try:
        rows = await _export_rows(session, request, access)
    except LogisticsSourceUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    payload = render_workbook(request, rows)
    quoted = quote(request.fileName)
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )


# ─── «Ставки» (the main table) ────────────────────────────────────────────


async def _record_rate_updates(
    session: AsyncSession,
    *,
    access: LogisticsAccess,
    applied: list[AppliedCell],
    period_date: date,
    session_id: str,
) -> None:
    """Journal one edit and fan it out to the period's collaboration room."""
    if not applied:
        return
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=LOGISTICS_PROJECT_KEY,
            section=LOGISTICS_SECTION_RATES,
            report_month=period_date,
            session_id=session_id,
            applied=applied,
        )
    await publish_cell_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_RATES,
        report_month=period_date.isoformat(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )


@router.get("/rates", response_model=LogisticsRateViewRead)
async def get_rates(
    session: SessionDependency,
    access: AccessDependency,
    month: Annotated[int, Query(ge=1, le=12)],
    year: Annotated[int, Query(ge=2025, le=2028)],
) -> LogisticsRateViewRead:
    """Return the «Ставки» view of one month/year (a worker sees only their assets)."""
    scope = access.asset_ids if access.role == ProjectRole.ASSET_WORKER else None
    try:
        return await read_rate_view(session, month=month, year=year, asset_ids=scope)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except LogisticsSourceUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.post("/rates", response_model=LogisticsRateRowRead, status_code=status.HTTP_201_CREATED)
async def post_rate(
    request: LogisticsRateCreateRequest,
    session: SessionDependency,
    access: EditorDependency,
    session_id: str = "",
) -> LogisticsRateRowRead:
    """Insert a complete rate row (the draft row is completed on the client)."""
    asset_id = request.assetId
    if access.role == ProjectRole.ASSET_WORKER:
        if asset_id is None:
            # The «Актив» column is hidden for a worker: the record always gets
            # the asset granted to them.
            asset_id = min(access.asset_ids)
        elif asset_id not in access.asset_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Добавлять ставки можно только по своему активу.",
            )
    elif asset_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Выберите актив.")
    request = request.model_copy(update={"assetId": asset_id})
    try:
        rate = await create_rate(session, request=request, editor=access.user)
        row = await read_rate_row(session, rate.id)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except LogisticsNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except LogisticsSourceUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    applied = [
        AppliedCell(
            target_table=LogisticsRate.__tablename__,
            row_id=rate.id,
            field="__created",
            old_value=None,
            new_value=rate_snapshot(rate),
        )
    ]
    await _record_rate_updates(
        session, access=access, applied=applied, period_date=rate.period_date, session_id=session_id
    )
    await publish_rows_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_RATES,
        report_month=rate.period_date.isoformat(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )
    return row


@router.patch("/rates/{rate_id}", response_model=LogisticsRateRowRead)
async def patch_rate(
    rate_id: UUID,
    request: LogisticsRateCellUpdate,
    session: SessionDependency,
    access: EditorDependency,
    session_id: str = "",
) -> LogisticsRateRowRead:
    """Edit one cell of a rate row (journal + room fan-out)."""
    scope = access.asset_ids if access.role == ProjectRole.ASSET_WORKER else None
    try:
        old_value, new_value = await update_rate_cell(
            session,
            rate_id=rate_id,
            field=request.field,
            value=request.value,
            editor=access.user,
            allowed_asset_ids=scope,
        )
        row = await read_rate_row(session, rate_id)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except LogisticsNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except LogisticsSourceUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    applied = [
        AppliedCell(
            target_table=LogisticsRate.__tablename__,
            row_id=rate_id,
            field=request.field,
            old_value=old_value,
            new_value=new_value,
        )
    ]
    await _record_rate_updates(
        session, access=access, applied=applied, period_date=row.periodDate, session_id=session_id
    )
    return row


@router.delete("/rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_endpoint(
    rate_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """Delete a rate row (journaled as an existence action; no dependents yet)."""
    try:
        period_date, snapshot = await delete_rate(session, rate_id=rate_id)
    except LogisticsNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if session_id:
        # The row is gone, so the journal carries its EXISTENCE: undo restores
        # the snapshot, redo deletes it again (the same action a create uses).
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=LOGISTICS_PROJECT_KEY,
            section=LOGISTICS_SECTION_RATES,
            report_month=period_date,
            session_id=session_id,
            applied=[
                AppliedCell(
                    target_table=LogisticsRate.__tablename__,
                    row_id=rate_id,
                    field="__created",
                    old_value=snapshot,
                    new_value=None,
                )
            ],
        )
    await publish_rows_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_RATES,
        report_month=period_date.isoformat(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )


@router.get("/rates/undo-state", response_model=UndoStateRead)
async def rates_undo_state(
    session: SessionDependency,
    access: EditorDependency,
    session_id: str = "",
    period_date: str = "",
    section: str = LOGISTICS_SECTION_RATES,
) -> UndoStateRead:
    """Undo/redo availability of the current editor's rates history."""
    scope = f"{section}/{period_date}"
    can_undo, can_redo = await JournalStore(session).undo_state(str(access.user.id), session_id, scope)
    return UndoStateRead(canUndo=can_undo > 0, canRedo=can_redo > 0)


@router.post("/rates/undo", response_model=UndoResponse)
async def rates_undo(
    session: SessionDependency,
    access: EditorDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Undo the current editor's last rates edit in the period."""
    section = request.section if request is not None and request.section else LOGISTICS_SECTION_RATES
    scope = f"{section}/{request.reportMonth if request is not None and request.reportMonth else ''}"
    outcome = await undo_last_edit(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=LOGISTICS_PROJECT_KEY,
    )
    return await _rates_undo_response(session, outcome, access=access)


@router.post("/rates/redo", response_model=UndoResponse)
async def rates_redo(
    session: SessionDependency,
    access: EditorDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Redo the current editor's oldest undone rates action in the period."""
    section = request.section if request is not None and request.section else LOGISTICS_SECTION_RATES
    scope = f"{section}/{request.reportMonth if request is not None and request.reportMonth else ''}"
    outcome = await redo_last_undo(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=LOGISTICS_PROJECT_KEY,
    )
    return await _rates_undo_response(session, outcome, access=access)


async def _rates_undo_response(
    session: AsyncSession,
    outcome: UndoOutcome,
    *,
    access: LogisticsAccess,
) -> UndoResponse:
    """Undo/redo outcome in the shared shape, published to the rates room."""
    if outcome.applied and outcome.applied_cells:
        await publish_cell_changed(
            JournalStore(session),
            project_key=LOGISTICS_PROJECT_KEY,
            section=outcome.section or LOGISTICS_SECTION_RATES,
            report_month=(outcome.report_month.isoformat() if outcome.report_month is not None else None),
            user_id=access.user.id,
            user_name=access.user.name or "",
            user_email=access.user.email or "",
            cells=outcome.applied_cells,
        )
    if not outcome.applied:
        conflict = outcome.conflict
        return UndoResponse(
            applied=False,
            reason=outcome.reason,
            conflicts=(
                [
                    UndoConflictRead(
                        rowId=conflict.row_id,
                        field=conflict.field,
                        user=conflict.user_name,
                        actionId=conflict.action_id,
                        reason=conflict.reason,
                    )
                ]
                if conflict is not None
                else []
            ),
        )
    cells = outcome.applied_cells
    if not cells:
        return UndoResponse(applied=True, reason="applied", section=outcome.section)
    return UndoResponse(
        applied=True,
        reason="applied",
        section=outcome.section,
        reportMonth=outcome.report_month,
        rowId=cells[0].row_id,
        field=cells[0].field,
        value=cells[0].new_value,
        revision=cells[0].revision,
    )


async def _record_logistics_user_updates(
    session: AsyncSession,
    *,
    access: LogisticsAccess,
    applied: list[AppliedCell],
    session_id: str,
) -> None:
    """Journal one users-dictionary action and fan it out to the section room."""
    if not applied:
        return
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=LOGISTICS_PROJECT_KEY,
            section=LOGISTICS_SECTION_USERS,
            report_month=None,
            session_id=session_id,
            applied=applied,
        )
    await publish_cell_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_USERS,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )


async def _users_undo_response(
    session: AsyncSession,
    outcome: UndoOutcome,
    *,
    access: LogisticsAccess,
) -> UndoResponse:
    """Undo/redo outcome in the shared shape, published to the users room."""
    if outcome.applied and outcome.applied_cells:
        await publish_cell_changed(
            JournalStore(session),
            project_key=LOGISTICS_PROJECT_KEY,
            section=LOGISTICS_SECTION_USERS,
            report_month=None,
            user_id=access.user.id,
            user_name=access.user.name or "",
            user_email=access.user.email or "",
            cells=outcome.applied_cells,
        )
        if any(cell.field in {"__created", "__deleted"} for cell in outcome.applied_cells):
            await publish_rows_changed(
                JournalStore(session),
                project_key=LOGISTICS_PROJECT_KEY,
                section=LOGISTICS_SECTION_USERS,
                report_month=None,
                user_id=access.user.id,
                user_name=access.user.name or "",
                user_email=access.user.email or "",
            )
    if not outcome.applied:
        conflict = outcome.conflict
        return UndoResponse(
            applied=False,
            reason=outcome.reason,
            conflicts=(
                [
                    UndoConflictRead(
                        rowId=conflict.row_id,
                        field=conflict.field,
                        user=conflict.user_name,
                        actionId=conflict.action_id,
                        reason=conflict.reason,
                    )
                ]
                if conflict is not None
                else []
            ),
        )
    cells = outcome.applied_cells
    if not cells:
        return UndoResponse(applied=True, reason="applied", section=outcome.section)
    return UndoResponse(
        applied=True,
        reason="applied",
        section=outcome.section,
        reportMonth=None,
        rowId=cells[0].row_id,
        field=cells[0].field,
        value=cells[0].new_value,
        revision=cells[0].revision,
    )


@router.get("/users/undo-state", response_model=UndoStateRead)
async def logistics_users_undo_state(
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> UndoStateRead:
    """Undo/redo availability of the users dictionary in this browser session."""
    can_undo, can_redo = await JournalStore(session).undo_state(
        str(access.user.id), session_id, f"{LOGISTICS_SECTION_USERS}/"
    )
    return UndoStateRead(canUndo=can_undo > 0, canRedo=can_redo > 0)


@router.post("/users/undo", response_model=UndoResponse)
async def logistics_users_undo(
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Undo the current admin's last users-dictionary action."""
    outcome = await undo_last_edit(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=f"{LOGISTICS_SECTION_USERS}/",
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=LOGISTICS_PROJECT_KEY,
    )
    return await _users_undo_response(session, outcome, access=access)


@router.post("/users/redo", response_model=UndoResponse)
async def logistics_users_redo(
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Redo the current admin's oldest undone users-dictionary action."""
    outcome = await redo_last_undo(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=f"{LOGISTICS_SECTION_USERS}/",
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=LOGISTICS_PROJECT_KEY,
    )
    return await _users_undo_response(session, outcome, access=access)


@router.post("/users", response_model=LogisticsUserRowRead, status_code=status.HTTP_201_CREATED)
async def post_logistics_user(
    request: LogisticsUserCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> LogisticsUserRowRead:
    try:
        row, applied = await create_logistics_user(session, request=request, editor=access.user)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _record_logistics_user_updates(session, access=access, applied=applied, session_id=session_id)
    await publish_rows_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_USERS,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )
    return row


@router.patch("/users/{user_id}", response_model=LogisticsUserRowRead)
async def patch_logistics_user(
    user_id: UUID,
    request: LogisticsUserUpdateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> LogisticsUserRowRead:
    try:
        row, applied = await update_logistics_user(session, user_id=user_id, request=request, editor=access.user)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except LogisticsNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _record_logistics_user_updates(session, access=access, applied=applied, session_id=session_id)
    return row


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_logistics_user(
    user_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    try:
        applied = await delete_logistics_user(session, user_id=user_id, editor=access.user)
    except LogisticsValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except LogisticsNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _record_logistics_user_updates(session, access=access, applied=applied, session_id=session_id)
    await publish_rows_changed(
        JournalStore(session),
        project_key=LOGISTICS_PROJECT_KEY,
        section=LOGISTICS_SECTION_USERS,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )
