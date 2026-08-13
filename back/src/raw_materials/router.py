"""API проекта «Сырьё»: чтение и правка форм «Движение» и «Цены», справочники."""

from collections.abc import Sequence
from datetime import date
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.collab.events import publish_cell_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse
from src.collab.store import get_collab_store
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.database import get_session
from src.raw_materials.approve import (
    ApprovalError,
    fix_report,
    read_movement_meta,
)
from src.raw_materials.export import (
    ExportValidationError,
    render_workbook,
    validate_export_request,
)
from src.raw_materials.models import MovementPeriod, RawMaterialAsset, ReportSnapshot, SnapshotMaterialMovement
from src.raw_materials.schemas import (
    AssetCreateRequest,
    AssetRowRead,
    AssetUpdateRequest,
    DeliveryBasisCreateRequest,
    DeliveryBasisRowRead,
    DeliveryBasisUpdateRequest,
    ExportViewRequest,
    FixMovementRequest,
    MovementMetaRead,
    MovementRowRead,
    MovementSnapshotRead,
    MovementUpdateRequest,
    PriceRowRead,
    PriceUpdateRequest,
    RawMaterialCreateRequest,
    RawMaterialRowRead,
    RawMaterialsMetaRead,
    RawMaterialUpdateRequest,
    SnapshotMovementRowRead,
    SnapshotViewRead,
    UserCreateRequest,
    UserRowRead,
    UserUpdateRequest,
)
from src.raw_materials.service import (
    CellValidationError,
    DictionaryReferenceError,
    NoRawMaterialsAccessError,
    create_asset,
    create_delivery_basis,
    create_project_user,
    create_raw_material,
    delete_asset,
    delete_delivery_basis,
    delete_project_user,
    delete_raw_material,
    read_asset_rows,
    read_delivery_basis_rows,
    read_meta,
    read_movement_rows,
    read_price_rows,
    read_raw_material_rows,
    read_snapshot_export_rows,
    read_user_rows,
    resolve_raw_materials_access,
    update_asset,
    update_delivery_basis,
    update_movement_cells,
    update_price_cells,
    update_project_user,
    update_raw_material,
)

router = APIRouter(prefix="/raw-materials", tags=["raw-materials"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
IdentityDependency = Annotated[CurrentUserIdentity, Depends(read_signed_identity)]


async def require_user(
    session: SessionDependency,
    identity: IdentityDependency,
) -> User:
    """Локальная учётная запись пользователя (для updated_by)."""
    user = await get_user_by_identity(session, identity)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пользователь не найден.",
        )
    return user


UserDependency = Annotated[User, Depends(require_user)]


async def require_raw_materials_access(
    session: SessionDependency,
    identity: IdentityDependency,
) -> list[RawMaterialAsset]:
    """Активы пользователя в проекте «Сырьё»; 403 при отсутствии доступа."""
    try:
        _, _, assets = await resolve_raw_materials_access(session, identity)
    except NoRawMaterialsAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Сырьё».",
        ) from error
    return list(assets)


AccessDependency = Annotated[list[RawMaterialAsset], Depends(require_raw_materials_access)]


async def require_raw_materials_admin(
    session: SessionDependency,
    identity: IdentityDependency,
) -> list[RawMaterialAsset]:
    """Только администратор проекта «Сырьё» (для справочников)."""
    try:
        _, roles, assets = await resolve_raw_materials_access(session, identity)
    except NoRawMaterialsAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Сырьё».",
        ) from error
    if ProjectRole.ADMIN not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «Сырьё».",
        )
    return list(assets)


AdminDependency = Annotated[list[RawMaterialAsset], Depends(require_raw_materials_admin)]


@router.get("/meta", response_model=RawMaterialsMetaRead)
async def get_raw_materials_meta(
    session: SessionDependency,
    assets: AccessDependency,
) -> RawMaterialsMetaRead:
    """Справочники форм: доступные активы, периоды движения, базисы поставки."""
    return await read_meta(session, assets)


@router.get("/movement", response_model=list[MovementRowRead])
async def get_movement_rows(
    session: SessionDependency,
    assets: AccessDependency,
    report_month: Annotated[date | None, Query()] = None,
) -> list[MovementRowRead]:
    """Строки формы «Движение»; без report_month — последний доступный период."""
    if report_month is None:
        report_month = await _latest_period(session)
    return await read_movement_rows(session, report_month, assets)


@router.get("/movement/meta", response_model=MovementMetaRead)
async def get_movement_meta(
    session: SessionDependency,
    _assets: AccessDependency,
    report_month: Annotated[date | None, Query()] = None,
) -> MovementMetaRead:
    """Статус формы «Движение»: полнота, финальность, архив зафиксированных версий."""
    if report_month is None:
        report_month = await _latest_period(session)
    is_complete, is_final, snapshots = await read_movement_meta(session, report_month=report_month)
    return MovementMetaRead(
        reportMonth=report_month,
        isComplete=is_complete,
        isFinal=is_final,
        snapshots=[
            MovementSnapshotRead(
                id=snapshot.id,
                reportMonth=snapshot.report_month,
                fixedAt=snapshot.fixed_at,
                fixedByFullname=snapshot.fixed_by_fullname,
                isFinal=snapshot.is_final,
            )
            for snapshot in snapshots
        ],
    )


@router.post("/movement/fix", response_model=MovementSnapshotRead)
async def post_movement_fix(
    request: FixMovementRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> MovementSnapshotRead:
    """Фиксация «Движения» за отчётный месяц (только администратор проекта)."""
    try:
        snapshot = await fix_report(
            session,
            user=user,
            report_month=request.reportMonth,
            store=get_collab_store(),
        )
    except ApprovalError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return MovementSnapshotRead(
        id=snapshot.id,
        reportMonth=snapshot.report_month,
        fixedAt=snapshot.fixed_at,
        fixedByFullname=snapshot.fixed_by_fullname,
        isFinal=snapshot.is_final,
    )


@router.get("/movement/snapshot/{snapshot_id}", response_model=SnapshotViewRead)
async def get_movement_snapshot(
    snapshot_id: UUID,
    session: SessionDependency,
    _assets: AccessDependency,
) -> SnapshotViewRead:
    """Зафиксированная версия «Движения» для просмотра (только чтение)."""
    snapshot = await session.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Снимок не найден.")
    rows = list(
        (
            await session.exec(
                select(SnapshotMaterialMovement)
                .where(col(SnapshotMaterialMovement.snapshot_id) == snapshot_id)
                .order_by(
                    SnapshotMaterialMovement.raw_material_name,
                    SnapshotMaterialMovement.asset_name,
                )
            )
        ).all()
    )
    return SnapshotViewRead(
        snapshot=MovementSnapshotRead(
            id=snapshot.id,
            reportMonth=snapshot.report_month,
            fixedAt=snapshot.fixed_at,
            fixedByFullname=snapshot.fixed_by_fullname,
            isFinal=snapshot.is_final,
        ),
        comparisonSnapshotId=snapshot.previous_snapshot_id,
        rows=[
            SnapshotMovementRowRead(
                sourceAssetRawMaterialId=row.source_asset_raw_material_id,
                assetName=row.asset_name,
                rawMaterialName=row.raw_material_name,
                businessPlanTons=row.business_plan_tons,
                demandPlanTons=row.demand_plan_tons,
                consumptionFactTons=row.consumption_fact_tons,
                demandDeviationTons=row.demand_deviation_tons,
                deliveryPlanTons=row.delivery_plan_tons,
                deliveryFactTons=row.delivery_fact_tons,
                deliveryDeviationTons=row.delivery_deviation_tons,
                balanceStartTons=row.balance_start_tons,
                balanceOnSiteTons=row.balance_on_site_tons,
                balanceInTransitTons=row.balance_in_transit_tons,
                balanceEndTons=row.balance_end_tons,
                storageStandardTons=row.storage_standard_tons,
                deviationReason=row.deviation_reason,
            )
            for row in rows
        ],
    )


@router.get("/prices", response_model=list[PriceRowRead])
async def get_price_rows(
    session: SessionDependency,
    assets: AccessDependency,
) -> list[PriceRowRead]:
    """Строки формы «Цены»."""
    return await read_price_rows(session, assets)


@router.get("/raw-materials", response_model=list[RawMaterialRowRead])
async def get_raw_materials(
    session: SessionDependency,
    _assets: AdminDependency,
) -> list[RawMaterialRowRead]:
    """Справочник «Сырьевые позиции»."""
    return await read_raw_material_rows(session)


@router.get("/assets", response_model=list[AssetRowRead])
async def get_assets(
    _session: SessionDependency,
    assets: AdminDependency,
) -> list[AssetRowRead]:
    """Справочник «Активы»."""
    return await read_asset_rows(assets)


@router.get("/delivery-bases", response_model=list[DeliveryBasisRowRead])
async def get_delivery_bases(
    session: SessionDependency,
    _assets: AdminDependency,
) -> list[DeliveryBasisRowRead]:
    """Справочник «Базисы поставки»."""
    return await read_delivery_basis_rows(session)


@router.get("/users", response_model=list[UserRowRead])
async def get_users(
    session: SessionDependency,
    _assets: AdminDependency,
) -> list[UserRowRead]:
    """Справочник «Пользователи»."""
    return await read_user_rows(session)


async def _latest_period(session: AsyncSession) -> date:
    period = (
        await session.exec(select(col(MovementPeriod.report_month)).order_by(col(MovementPeriod.report_month).desc()))
    ).first()
    if period is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет периодов движения.")
    return period


DICTIONARY_SECTIONS = frozenset({"rawMaterials", "assets", "deliveryBases", "users"})
EXPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/export")
async def export_table(
    request: ExportViewRequest,
    session: SessionDependency,
    identity: IdentityDependency,
) -> Response:
    """Выгрузка таблицы в Excel: «вся» — дефолтный вид, «видимая» — переданные строки."""
    try:
        _, roles, user_assets = await resolve_raw_materials_access(session, identity)
    except NoRawMaterialsAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Сырьё».",
        ) from error
    is_admin = ProjectRole.ADMIN in roles
    if request.section in DICTIONARY_SECTIONS and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «Сырьё».",
        )
    if request.snapshotId is not None and request.section != "materialMovement":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Архивную версию можно выгрузить только для формы «Движение».",
        )
    try:
        validate_export_request(request, show_asset_column=is_admin or len(user_assets) > 1)
    except ExportValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    rows = await _export_rows(session, request, user_assets)
    if request.rowIds is not None:
        rows = _reorder_rows(rows, request.rowIds)
    asset_names = [asset.name for asset in user_assets]
    delivery_bases = [basis.name for basis in await read_delivery_basis_rows(session)]
    payload = await run_in_threadpool(render_workbook, request, rows, asset_names, delivery_bases)
    return Response(
        content=payload,
        media_type=EXPORT_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(request.fileName.strip())}"},
    )


async def _export_rows(
    session: AsyncSession,
    request: ExportViewRequest,
    assets: list[RawMaterialAsset],
) -> Sequence[BaseModel]:
    """Строки для выгрузки: движение за период, цены или справочник."""
    if request.section == "materialMovement":
        if request.snapshotId is not None:
            snapshot, rows = await read_snapshot_export_rows(session, request.snapshotId)
            if snapshot is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Снимок не найден.",
                )
            return rows
        report_month = request.reportMonth or await _latest_period(session)
        return await read_movement_rows(session, report_month, assets)
    if request.section == "rawMaterialPrices":
        return await read_price_rows(session, assets)
    if request.section == "rawMaterials":
        return await read_raw_material_rows(session)
    if request.section == "assets":
        return await read_asset_rows(assets)
    if request.section == "deliveryBases":
        return await read_delivery_basis_rows(session)
    return await read_user_rows(session)


def _reorder_rows(rows: Sequence[BaseModel], row_ids: Sequence[UUID]) -> list[BaseModel]:
    """Видимая выборка: только переданные id, в порядке грида, пропуская отсутствующие."""
    by_id = {getattr(row, "id"): row for row in rows}  # noqa: B009
    return [by_id[row_id] for row_id in row_ids if row_id in by_id]


@router.patch("/movement")
async def patch_movement_rows(
    request: MovementUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    assets: AccessDependency,
) -> int:
    """Пакет правок ячеек формы «Движение» за отчётный месяц."""
    try:
        applied = await update_movement_cells(
            session,
            report_month=request.reportMonth,
            updates=request.updates,
            user=user,
            assets=assets,
        )
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="materialMovement",
        report_month=request.reportMonth.isoformat(),
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    return len(applied)


@router.patch("/prices")
async def patch_price_rows(
    request: PriceUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    assets: AccessDependency,
) -> int:
    """Пакет правок ячеек формы «Цены»."""
    try:
        applied = await update_price_cells(session, updates=request.updates, user=user, assets=assets)
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="rawMaterialPrices",
        report_month=None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    return len(applied)


@router.post("/undo", response_model=UndoResponse)
async def undo_cells(
    session: SessionDependency,
    user: UserDependency,
    _assets: AccessDependency,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """
    Отменяет последнюю правку ячеек текущего пользователя.

    Тело запроса опционально: skipActionId передаётся вторым нажатием
    после конфликта, чтобы пропустить заблокированное действие.
    """
    outcome = await undo_last_edit(
        session,
        user=user,
        store=get_collab_store(),
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(outcome, user)


@router.post("/redo", response_model=UndoResponse)
async def redo_cells(
    session: SessionDependency,
    user: UserDependency,
    _assets: AccessDependency,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Возвращает последнее отменённое действие текущего пользователя."""
    outcome = await redo_last_undo(
        session,
        user=user,
        store=get_collab_store(),
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(outcome, user)


async def _undo_response(outcome: UndoOutcome, user: User) -> UndoResponse:
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
    cell = outcome.applied_cell
    if cell is None:
        return UndoResponse(applied=True, reason="applied", section=outcome.section, reportMonth=outcome.report_month)
    await publish_cell_changed(
        get_collab_store(),
        project_key=outcome.project_key,
        section=outcome.section,
        report_month=outcome.report_month.isoformat() if outcome.report_month is not None else None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=[
            AppliedCell(
                target_table=cell.target_table,
                row_id=cell.row_id,
                field=cell.field,
                old_value=cell.old_value,
                new_value=outcome.applied_value,
                revision=cell.revision,
                action_id=cell.action_id,
            )
        ],
    )
    return UndoResponse(
        applied=True,
        reason="applied",
        section=outcome.section,
        reportMonth=outcome.report_month,
        rowId=cell.row_id,
        field=cell.field,
        value=outcome.applied_value,
        revision=cell.revision,
    )


@router.post("/raw-materials", response_model=RawMaterialRowRead, status_code=status.HTTP_201_CREATED)
async def post_raw_material(
    request: RawMaterialCreateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> RawMaterialRowRead:
    """Создание сырьевой позиции (только админ проекта)."""
    try:
        created = await create_raw_material(session, name=request.name, asset_ids=request.assetIds, user=user)
    except DictionaryReferenceError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return (await read_raw_material_rows(session, raw_material_id=created.id))[0]


@router.patch("/raw-materials/{raw_material_id}", response_model=RawMaterialRowRead)
async def patch_raw_material(
    raw_material_id: UUID,
    request: RawMaterialUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> RawMaterialRowRead:
    """Правка сырьевой позиции (только админ проекта)."""
    try:
        _, applied = await update_raw_material(
            session,
            raw_material_id=raw_material_id,
            name=request.name,
            asset_ids=request.assetIds,
            user=user,
        )
    except DictionaryReferenceError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="rawMaterials",
        report_month=None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="rawMaterials",
        report_month=None,
        applied=applied,
    )
    return (await read_raw_material_rows(session, raw_material_id=raw_material_id))[0]


@router.delete("/raw-materials/{raw_material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_raw_material_endpoint(
    raw_material_id: UUID,
    session: SessionDependency,
    _user: UserDependency,
    _assets: AdminDependency,
) -> None:
    """Удаление сырьевой позиции (только админ проекта)."""
    try:
        await delete_raw_material(session, raw_material_id=raw_material_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/assets", response_model=AssetRowRead, status_code=status.HTTP_201_CREATED)
async def post_asset(
    request: AssetCreateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> AssetRowRead:
    """Создание актива (только админ проекта)."""
    created = await create_asset(session, name=request.name, user=user)
    return (await read_asset_rows([created]))[0]


@router.patch("/assets/{asset_id}", response_model=AssetRowRead)
async def patch_asset(
    asset_id: UUID,
    request: AssetUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> AssetRowRead:
    """Правка актива (только админ проекта)."""
    try:
        updated, applied = await update_asset(session, asset_id=asset_id, name=request.name, user=user)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="assets",
        report_month=None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="assets",
        report_month=None,
        applied=applied,
    )
    return (await read_asset_rows([updated]))[0]


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset_endpoint(
    asset_id: UUID,
    session: SessionDependency,
    _user: UserDependency,
    _assets: AdminDependency,
) -> None:
    """Удаление актива (только админ проекта)."""
    try:
        await delete_asset(session, asset_id=asset_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/delivery-bases", response_model=DeliveryBasisRowRead, status_code=status.HTTP_201_CREATED)
async def post_delivery_basis(
    request: DeliveryBasisCreateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> DeliveryBasisRowRead:
    """Создание базиса поставки (только админ проекта)."""
    created = await create_delivery_basis(session, name=request.name, user=user)
    return (await read_delivery_basis_rows(session, delivery_basis_id=created.id))[0]


@router.patch("/delivery-bases/{basis_id}", response_model=DeliveryBasisRowRead)
async def patch_delivery_basis(
    basis_id: UUID,
    request: DeliveryBasisUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> DeliveryBasisRowRead:
    """Правка базиса поставки (только админ проекта)."""
    try:
        updated, applied = await update_delivery_basis(session, basis_id=basis_id, name=request.name, user=user)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="deliveryBases",
        report_month=None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="deliveryBases",
        report_month=None,
        applied=applied,
    )
    return (await read_delivery_basis_rows(session, delivery_basis_id=updated.id))[0]


@router.delete("/delivery-bases/{basis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delivery_basis_endpoint(
    basis_id: UUID,
    session: SessionDependency,
    _user: UserDependency,
    _assets: AdminDependency,
) -> None:
    """Удаление базиса поставки (только админ проекта)."""
    try:
        await delete_delivery_basis(session, basis_id=basis_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/users", response_model=UserRowRead, status_code=status.HTTP_201_CREATED)
async def post_user(
    request: UserCreateRequest,
    session: SessionDependency,
    _user: UserDependency,
    _assets: AdminDependency,
) -> UserRowRead:
    """Создание пользователя проекта «Сырьё» (только админ проекта)."""
    project_id = await _raw_materials_project_id(session)
    try:
        created = await create_project_user(
            session,
            project_id=project_id,
            email=request.email,
            fullname=request.fullname,
            is_admin=request.isAdmin,
            asset_names=request.assetNames,
        )
    except DictionaryReferenceError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return (await read_user_rows(session, user_id=created.id))[0]


@router.patch("/users/{user_id}", response_model=UserRowRead)
async def patch_user(
    user_id: UUID,
    request: UserUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> UserRowRead:
    """Правка пользователя проекта «Сырьё» (только админ проекта)."""
    project_id = await _raw_materials_project_id(session)
    try:
        _, applied = await update_project_user(
            session,
            project_id=project_id,
            user_id=user_id,
            email=request.email,
            fullname=request.fullname,
            is_admin=request.isAdmin,
            asset_names=request.assetNames,
            is_active=request.isActive,
            reset_password=request.resetPassword,
            updated_by=user.name or user.email or "",
        )
    except DictionaryReferenceError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="users",
        report_month=None,
        user_id=user.id,
        user_name=user.name or "",
        user_email=user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="users",
        report_month=None,
        applied=applied,
    )
    return (await read_user_rows(session, user_id=user_id))[0]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_endpoint(
    user_id: UUID,
    session: SessionDependency,
    _user: UserDependency,
    _assets: AdminDependency,
) -> None:
    """Удаление пользователя проекта «Сырьё» (только админ проекта)."""
    project_id = await _raw_materials_project_id(session)
    try:
        await delete_project_user(session, project_id=project_id, user_id=user_id)
    except DictionaryReferenceError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


async def _raw_materials_project_id(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект «Сырьё» не найден.")
    return project_id
