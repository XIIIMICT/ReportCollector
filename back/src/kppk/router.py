"""«КППК» project API: the source-copy dictionaries of this phase."""

from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import UserRole
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.collab.events import publish_cell_changed, publish_rows_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.persistence import JournalStore
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.common.export import ExportValidationError, safe_sheet_name
from src.common.export_jobs import ExportJobRead, export_table_or_job, table_export_jobs
from src.database import get_session
from src.downloads import XLSX_MEDIA_TYPE
from src.kppk.constants import (
    KPPK_PROJECT_KEY,
    KPPK_SECTION_ORGS,
    KPPK_SECTION_PRODUCT_POSITIONS,
    KPPK_SECTION_RAWS,
    KPPK_SECTION_REALIZATION_FACT,
    KPPK_SECTION_REALIZATION_PLAN,
    KPPK_SECTION_USERS,
)

from .core import kppk_project_id
from .dictionaries import (
    create_org,
    create_product_position,
    create_raw,
    delete_org,
    delete_product_position,
    delete_raw,
    read_org_names,
    read_org_rows,
    read_product_position_rows,
    read_raw_rows,
    update_org_cell,
    update_product_position_cell,
    update_raw_cell,
)
from .export import REFERENCE_LISTS, SECTION_SHEET_NAMES, validate_export_request
from .models import KppkRealization, KppkRealizationPlan
from .realization import (
    read_current_period,
    read_market_codes,
    read_realization_row_by_id,
    read_realization_rows,
    update_realization_cell,
)
from .schemas import (
    KppkAppliedRead,
    KppkExportViewRequest,
    KppkOrgCellUpdate,
    KppkOrgCreateRequest,
    KppkOrgRowRead,
    KppkPeriodCellUpdate,
    KppkPeriodCreateRequest,
    KppkPeriodRowRead,
    KppkProductPositionCellUpdate,
    KppkProductPositionCreateRequest,
    KppkProductPositionRowRead,
    KppkRawCellUpdate,
    KppkRawCreateRequest,
    KppkRawRowRead,
    KppkRealizationCellUpdate,
    KppkRealizationPeriodRead,
    KppkRealizationViewRead,
    KppkUserCreateRequest,
    KppkUserRowRead,
    KppkUserUpdateRequest,
)
from .service import (
    KppkNotFoundError,
    KppkValidationError,
    NoKppkAccessError,
    create_period,
    delete_period,
    is_kppk_admin,
    read_period_rows,
    resolve_kppk_access,
    update_period_cell,
)
from .users import create_kppk_user, delete_kppk_project_user, read_kppk_user_rows, update_kppk_user

if TYPE_CHECKING:
    from src.auth.models import ProjectRole, User

router = APIRouter(prefix="/kppk", tags=["kppk"])

SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
IdentityDependency = Annotated[CurrentUserIdentity, Depends(read_signed_identity)]


class KppkAccess:
    """Resolved access of the current user to the «КППК» project."""

    def __init__(self, *, user: User, roles: list[ProjectRole]) -> None:
        self.user = user
        self.roles = roles
        self.is_admin = is_kppk_admin(roles)


async def require_kppk_access(
    session: SessionDependency,
    identity: IdentityDependency,
) -> KppkAccess:
    """Resolve the user's КППК access; 403 without access to the project."""
    try:
        user, roles = await resolve_kppk_access(session, identity)
    except NoKppkAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «КППК».",
        ) from error
    return KppkAccess(user=user, roles=roles)


AccessDependency = Annotated[KppkAccess, Depends(require_kppk_access)]


async def require_kppk_admin(access: AccessDependency) -> KppkAccess:
    """Require the project admin role for dictionary mutations."""
    if not access.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «КППК».",
        )
    return access


AdminDependency = Annotated[KppkAccess, Depends(require_kppk_admin)]


async def _record_kppk_updates(
    session: AsyncSession,
    *,
    access: KppkAccess,
    section: str,
    session_id: str,
    applied: list[AppliedCell],
) -> None:
    """
    Journal the applied cells as one undo action and fan them out to the room.

    The scope is the tab id (the report month is always empty in «КППК»); the
    collaboration room is keyed the same way. Without a session id nothing is
    journaled — the history is page-lifetime, like in the other projects — but
    the other members still see the change live.
    """
    if not applied:
        return
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=KPPK_PROJECT_KEY,
            section=section,
            report_month=None,
            session_id=session_id,
            applied=applied,
        )
    await publish_cell_changed(
        JournalStore(session),
        project_key=KPPK_PROJECT_KEY,
        section=section,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )


# ─── Undo/redo (the shared collaboration mechanism) ───────────────────────


@router.get("/undo-state", response_model=UndoStateRead)
async def undo_state_cells(
    session: SessionDependency,
    access: AccessDependency,
    session_id: str = "",
    section: str = "",
) -> UndoStateRead:
    """Undo/redo availability from the user's session action stack on the current tab."""
    scope = f"{section}/"
    can_undo, can_redo = await JournalStore(session).undo_state(str(access.user.id), session_id, scope)
    return UndoStateRead(canUndo=can_undo > 0, canRedo=can_redo > 0)


@router.post("/undo", response_model=UndoResponse)
async def undo_cells(
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """
    Undo the current user's last dictionary edit on the current tab.

    The request body is optional: section names the tab whose history is
    walked, skipActionId is the action the user chose to skip after a conflict.
    """
    scope = f"{request.section if request is not None and request.section else ''}/"
    outcome = await undo_last_edit(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=KPPK_PROJECT_KEY,
    )
    return await _undo_response(session, outcome, access=access)


@router.post("/redo", response_model=UndoResponse)
async def redo_cells(
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Redo the current user's oldest undone action on the current tab."""
    scope = f"{request.section if request is not None and request.section else ''}/"
    outcome = await redo_last_undo(
        session,
        user=access.user,
        store=JournalStore(session),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
        project_key=KPPK_PROJECT_KEY,
    )
    return await _undo_response(session, outcome, access=access)


async def _undo_response(
    session: AsyncSession,
    outcome: UndoOutcome,
    *,
    access: KppkAccess,
) -> UndoResponse:
    """
    Undo/redo outcome as the shared response shape.

    An applied action is also published to the tab's room so other admins see
    the reverted cells live; the client additionally refreshes its grid.
    """
    if outcome.applied and outcome.applied_cells:
        await publish_cell_changed(
            JournalStore(session),
            project_key=KPPK_PROJECT_KEY,
            section=outcome.section or "",
            report_month=None,
            user_id=access.user.id,
            user_name=access.user.name or "",
            user_email=access.user.email or "",
            cells=outcome.applied_cells,
        )
        if outcome.section == KPPK_SECTION_USERS and any(
            cell.field in {"__created", "__deleted"} for cell in outcome.applied_cells
        ):
            await publish_rows_changed(
                JournalStore(session),
                project_key=KPPK_PROJECT_KEY,
                section=KPPK_SECTION_USERS,
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
        return UndoResponse(applied=True, reason="applied", section=outcome.section, reportMonth=None)
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


# ─── «Продуктовые позиции» ────────────────────────────────────────────────


@router.get("/product-positions", response_model=list[KppkProductPositionRowRead])
async def get_product_positions(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[KppkProductPositionRowRead]:
    """«Продуктовые позиции»: products with their ДЗО set (Матрица продуктов в БД)."""
    return await read_product_position_rows(session)


@router.post("/product-positions", response_model=KppkAppliedRead, status_code=status.HTTP_201_CREATED)
async def post_product_positions(
    request: KppkProductPositionCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Create a product with its ДЗО set."""
    try:
        row, applied = await create_product_position(session, request=request, user=access.user)
    except KppkValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_PRODUCT_POSITIONS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=row)


@router.patch("/product-positions/{product_id}", response_model=KppkAppliedRead)
async def patch_product_position(
    product_id: UUID,
    request: KppkProductPositionCellUpdate,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Edit one cell of a «Продуктовые позиции» row (name / orgNames / productNum)."""
    try:
        applied = await update_product_position_cell(
            session, product_id=product_id, field=request.field, value=request.value, user=access.user
        )
        updated = next(row for row in await read_product_position_rows(session) if row.id == product_id)
    except (KppkValidationError, KppkNotFoundError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_PRODUCT_POSITIONS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=updated)


@router.delete("/product-positions/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_product_position(
    product_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """Delete a product together with its ДЗО links."""
    try:
        applied = await delete_product_position(session, product_id=product_id)
    except KppkNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_PRODUCT_POSITIONS,
        session_id=session_id,
        applied=applied,
    )


# ─── «Сырьевые позиции» ───────────────────────────────────────────────────


@router.get("/raws", response_model=list[KppkRawRowRead])
async def get_raws(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[KppkRawRowRead]:
    """«Сырьевые позиции»: the raw list of the source dictionary."""
    return await read_raw_rows(session)


@router.post("/raws", response_model=KppkAppliedRead, status_code=status.HTTP_201_CREATED)
async def post_raws(
    request: KppkRawCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Create a raw item."""
    try:
        row, applied = await create_raw(session, name=request.name, raw_num=request.rawNum, user=access.user)
    except KppkValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_RAWS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=row)


@router.patch("/raws/{raw_id}", response_model=KppkAppliedRead)
async def patch_raw(
    raw_id: UUID,
    request: KppkRawCellUpdate,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Edit one cell of a raw row (name / rawNum)."""
    try:
        applied = await update_raw_cell(
            session, raw_id=raw_id, field=request.field, value=request.value, user=access.user
        )
        updated = next(row for row in await read_raw_rows(session) if row.id == raw_id)
    except (KppkValidationError, KppkNotFoundError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_RAWS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=updated)


@router.delete("/raws/{raw_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_raw(
    raw_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """Delete a raw item."""
    try:
        applied = await delete_raw(session, raw_id=raw_id)
    except KppkNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_RAWS,
        session_id=session_id,
        applied=applied,
    )


# ─── «ДЗО» ────────────────────────────────────────────────────────────────


@router.get("/orgs", response_model=list[KppkOrgRowRead])
async def get_orgs(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[KppkOrgRowRead]:
    """«ДЗО»: the enterprise dictionary of the source copies."""
    return await read_org_rows(session)


@router.post("/orgs", response_model=KppkAppliedRead, status_code=status.HTTP_201_CREATED)
async def post_orgs(
    request: KppkOrgCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Create a ДЗО."""
    try:
        row, applied = await create_org(session, code=request.code, name=request.name, user=access.user)
    except KppkValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_ORGS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=row)


@router.patch("/orgs/{org_id}", response_model=KppkAppliedRead)
async def patch_org(
    org_id: UUID,
    request: KppkOrgCellUpdate,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> KppkAppliedRead:
    """Edit one cell of a ДЗО row."""
    try:
        applied = await update_org_cell(
            session, org_id=org_id, field=request.field, value=request.value, user=access.user
        )
        updated = next(row for row in await read_org_rows(session) if row.id == org_id)
    except (KppkValidationError, KppkNotFoundError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_ORGS,
        session_id=session_id,
        applied=applied,
    )
    return KppkAppliedRead(row=updated)


@router.delete("/orgs/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_org(
    org_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """Delete a ДЗО that is not referenced."""
    try:
        applied = await delete_org(session, org_id=org_id)
    except (KppkNotFoundError, KppkValidationError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    await _record_kppk_updates(
        session,
        access=access,
        section=KPPK_SECTION_ORGS,
        session_id=session_id,
        applied=applied,
    )


# ─── «Реализация» (Таблицы.xlsx sheet «Реализация») ──────────────────────


_REALIZATION_SLICE_SECTION: dict[str, str] = {
    "bp": KPPK_SECTION_REALIZATION_PLAN,
    "op": KPPK_SECTION_REALIZATION_PLAN,
    "fact": KPPK_SECTION_REALIZATION_FACT,
}
_RealizationSlice = Literal["bp", "op", "fact"]


@router.get("/realization", response_model=KppkRealizationViewRead)
async def get_realization(
    session: SessionDependency,
    _access: AccessDependency,
    org_id: Annotated[UUID, Query(alias="orgId")],
    slice_id: Annotated[_RealizationSlice, Query(alias="slice")],
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> KppkRealizationViewRead:
    """Return the «Реализация» view of one slice and period (month+year; default: the data's newest)."""
    # A month+year the «Периоды» dictionary does not hold is a bad request, not
    # a crash: the picker offers future years (e.g. 2027) that may be absent.
    try:
        period = await read_current_period(session, month=month, year=year)
        rows = await read_realization_rows(session, slice_id=slice_id, org_id=org_id)
    except (KppkValidationError, KppkNotFoundError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    return KppkRealizationViewRead(
        period=KppkRealizationPeriodRead(
            id=period.id,
            monthName=period.month_name,
            yearNum=period.year_num,
            repDate=period.last_date,
        ),
        markets=await read_market_codes(session),
        rows=rows,
    )


@router.patch("/realization/{row_id}", response_model=KppkAppliedRead)
async def patch_realization(
    row_id: UUID,
    request: KppkRealizationCellUpdate,
    session: SessionDependency,
    access: AdminDependency,
    slice_id: Annotated[_RealizationSlice, Query(alias="slice")],
    session_id: str = "",
) -> KppkAppliedRead:
    """Edit one numeric cell of a «Реализация» row (journal + room fan-out)."""
    old_value, stored = await update_realization_cell(
        session,
        slice_id=slice_id,
        row_id=row_id,
        field=request.field,
        value=request.value,
        user=access.user,
    )
    model = KppkRealization if slice_id == "fact" else KppkRealizationPlan
    applied = [
        AppliedCell(
            target_table=model.__tablename__,
            row_id=row_id,
            field=request.field,
            # The journal is JSON: the Decimal cell values travel as floats.
            old_value=old_value if old_value is None else float(old_value),
            new_value=stored if stored is None else float(stored),
        )
    ]
    await _record_kppk_updates(
        session,
        access=access,
        section=_REALIZATION_SLICE_SECTION[slice_id],
        session_id=session_id,
        applied=applied,
    )
    row = await read_realization_row_by_id(session, slice_id=slice_id, row_id=row_id)
    if row is None:
        message = "Строка не найдена."
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from None
    return KppkAppliedRead(row=row)


# ─── «Периоды» (backend-only in this phase) ───────────────────────────────


@router.get("/periods", response_model=list[KppkPeriodRowRead])
async def get_periods(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[KppkPeriodRowRead]:
    """«Периоды»: the report months of the source dictionary."""
    return await read_period_rows(session)


@router.post("/periods", response_model=KppkAppliedRead, status_code=status.HTTP_201_CREATED)
async def post_periods(
    request: KppkPeriodCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
) -> KppkAppliedRead:
    """Create a report month."""
    try:
        row = await create_period(
            session,
            month_name=request.monthName,
            year_num=request.yearNum,
            first_date=request.firstDate,
            last_date=request.lastDate,
            user=access.user,
        )
    except KppkValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return KppkAppliedRead(row=row)


@router.patch("/periods/{period_id}", response_model=KppkAppliedRead)
async def patch_period(
    period_id: UUID,
    request: KppkPeriodCellUpdate,
    session: SessionDependency,
    access: AdminDependency,
) -> KppkAppliedRead:
    """Edit one cell of a period row."""
    try:
        await update_period_cell(
            session, period_id=period_id, field=request.field, value=request.value, user=access.user
        )
        updated = next(row for row in await read_period_rows(session) if row.id == period_id)
    except (KppkValidationError, KppkNotFoundError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    return KppkAppliedRead(row=updated)


@router.delete("/periods/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_period(
    period_id: UUID,
    session: SessionDependency,
    _access: AdminDependency,
) -> None:
    """Delete a period that is not referenced by the source copies."""
    try:
        await delete_period(session, period_id=period_id)
    except (KppkNotFoundError, KppkValidationError) as error:
        status_code = status.HTTP_404_NOT_FOUND if isinstance(error, KppkNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(error)) from error


# ─── Участники проекта ────────────────────────────────────────────────────


@router.get("/users", response_model=list[KppkUserRowRead])
async def get_kppk_users(
    session: SessionDependency,
    _access: AdminDependency,
) -> list[KppkUserRowRead]:
    """«Пользователи» of the «КППК» project (admin only)."""
    return await read_kppk_user_rows(session)


@router.post("/users", response_model=KppkUserRowRead, status_code=status.HTTP_201_CREATED)
async def post_kppk_user(
    request: KppkUserCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> KppkUserRowRead:
    try:
        person, applied = await create_kppk_user(session, request=request, editor=access.user)
    except KppkValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await _record_kppk_updates(
        session, access=access, section=KPPK_SECTION_USERS, session_id=session_id, applied=applied
    )
    await publish_rows_changed(
        JournalStore(session),
        project_key=KPPK_PROJECT_KEY,
        section=KPPK_SECTION_USERS,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )
    return next(row for row in await read_kppk_user_rows(session) if row.id == person.id)


@router.patch("/users/{user_id}", response_model=KppkUserRowRead)
async def patch_kppk_user(
    user_id: UUID,
    request: KppkUserUpdateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> KppkUserRowRead:
    try:
        applied = await update_kppk_user(session, user_id=user_id, request=request, editor=access.user)
    except KppkNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except KppkValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await _record_kppk_updates(
        session, access=access, section=KPPK_SECTION_USERS, session_id=session_id, applied=applied
    )
    return next(row for row in await read_kppk_user_rows(session) if row.id == user_id)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kppk_user(
    user_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """
    Remove a user from the «КППК» project (admin only).

    The КППК subscription is its `UserRole` link; the account is soft-deleted
    when no project link is left, so the person disappears from every
    «Пользователи» list instead of lingering as an empty row. An admin cannot
    remove themselves — the last admin would otherwise lock the project out.
    """
    if user_id == access.user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя удалить себя из проекта.",
        )
    project_id = await kppk_project_id(session)
    link = await session.get(UserRole, (user_id, project_id))
    if link is not None and link.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала снимите доступ пользователя к проекту, затем удаляйте.",
        )
    try:
        before = next((row for row in await read_kppk_user_rows(session) if row.id == user_id), None)
        await delete_kppk_project_user(session, user_id=user_id)
    except KppkNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователя нельзя удалить: на нём другие данные проекта.",
        ) from error
    if before is not None:
        await _record_kppk_updates(
            session,
            access=access,
            section=KPPK_SECTION_USERS,
            session_id=session_id,
            applied=[
                AppliedCell(
                    target_table="app_users",
                    row_id=user_id,
                    field="__deleted",
                    old_value={"role": "admin" if before.isAdmin else "asset_worker", "isActive": before.isActive},
                    new_value=None,
                )
            ],
        )
    await publish_rows_changed(
        JournalStore(session),
        project_key=KPPK_PROJECT_KEY,
        section=KPPK_SECTION_USERS,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )


# ─── Excel export ─────────────────────────────────────────────────────────


@router.post("/export")
async def export_kppk_table(
    request: KppkExportViewRequest,
    session: SessionDependency,
    access: AccessDependency,
) -> Response:
    """
    Excel export of a dictionary tab: «вся» — all rows, «видимая» — the given ones.

    Small tables render straight into this response; past
    ``INLINE_EXPORT_MAX_ROWS`` the response is 202 + a job description and
    the workbook is built by the shared background worker.
    """
    try:
        validate_export_request(request)
    except ExportValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if request.section == KPPK_SECTION_PRODUCT_POSITIONS:
        rows: list[KppkProductPositionRowRead | KppkRawRowRead | KppkOrgRowRead | KppkUserRowRead] = list(
            await read_product_position_rows(session)
        )
    elif request.section == KPPK_SECTION_RAWS:
        rows = list(await read_raw_rows(session))
    elif request.section == KPPK_SECTION_USERS:
        if not access.is_admin:
            raise HTTPException(status_code=403, detail="Требуются права администратора проекта «КППК».")
        rows = list(await read_kppk_user_rows(session))
    else:
        rows = list(await read_org_rows(session))
    if request.rowIds is not None:
        by_id = {row.id: row for row in rows}
        rows = [by_id[row_id] for row_id in request.rowIds if row_id in by_id]
    org_names = await read_org_names(session)
    needs_lists = any(column.key in REFERENCE_LISTS for column in request.columns)
    return await export_table_or_job(
        sheet_title=safe_sheet_name(SECTION_SHEET_NAMES[request.section]),
        columns=request.columns,
        rows=rows,
        header_pinned=request.headerPinned,
        file_name=request.fileName,
        user_id=str(access.user.id),
        lists={"orgs": org_names} if needs_lists else None,
        column_lists=REFERENCE_LISTS,
    )


@router.get("/export/jobs/{job_id}", response_model=ExportJobRead)
async def get_kppk_export_job(
    job_id: str,
    _access: AccessDependency,
) -> ExportJobRead:
    """Status of a background table export (progress / done / failed)."""
    try:
        job = table_export_jobs.get(job_id, str(_access.user.id))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание выгрузки не найдено.") from error
    return ExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
    )


@router.get("/export/jobs/{job_id}/file")
async def get_kppk_export_job_file(
    job_id: str,
    access: AccessDependency,
) -> StreamingResponse:
    """Stream the XLSX of a background table export AS it is being generated."""
    user_id = str(access.user.id)
    try:
        job = table_export_jobs.get(job_id, user_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задание выгрузки не найдено.",
        ) from error
    if job.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Не удалось сформировать файл выгрузки.",
        )
    quoted = quote(job.spec.file_name)
    return StreamingResponse(
        table_export_jobs.stream_growing(job_id, user_id),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )


_SECTION_LABELS: dict[str, str] = {
    "productPositions": "Продуктовые позиции",
    "rawPositions": "Сырьевые позиции",
    "orgs": "ДЗО",
    "periods": "Периоды",
}


def section_label(section: Literal["productPositions", "rawPositions", "orgs", "periods"]) -> str:
    """Russian label for a dictionary section id."""
    return _SECTION_LABELS.get(section, section)
