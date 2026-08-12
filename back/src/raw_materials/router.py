"""API проекта «Сырьё»: чтение и правка форм «Движение» и «Цены», справочники."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.database import get_session
from src.raw_materials.models import MovementPeriod, RawMaterialAsset
from src.raw_materials.schemas import (
    AssetCreateRequest,
    AssetRowRead,
    AssetUpdateRequest,
    DeliveryBasisCreateRequest,
    DeliveryBasisRowRead,
    DeliveryBasisUpdateRequest,
    MovementRowRead,
    MovementUpdateRequest,
    PriceRowRead,
    PriceUpdateRequest,
    RawMaterialCreateRequest,
    RawMaterialRowRead,
    RawMaterialsMetaRead,
    RawMaterialUpdateRequest,
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


@router.patch("/movement")
async def patch_movement_rows(
    request: MovementUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    assets: AccessDependency,
) -> int:
    """Пакет правок ячеек формы «Движение» за отчётный месяц."""
    try:
        return await update_movement_cells(
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


@router.patch("/prices")
async def patch_price_rows(
    request: PriceUpdateRequest,
    session: SessionDependency,
    user: UserDependency,
    assets: AccessDependency,
) -> int:
    """Пакет правок ячеек формы «Цены»."""
    try:
        return await update_price_cells(session, updates=request.updates, user=user, assets=assets)
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/raw-materials", response_model=RawMaterialRowRead, status_code=status.HTTP_201_CREATED)
async def post_raw_material(
    request: RawMaterialCreateRequest,
    session: SessionDependency,
    user: UserDependency,
    _assets: AdminDependency,
) -> RawMaterialRowRead:
    """Создание сырьевой позиции (только админ проекта)."""
    created = await create_raw_material(session, name=request.name, asset_ids=request.assetIds, user=user)
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
        await update_raw_material(
            session,
            raw_material_id=raw_material_id,
            name=request.name,
            asset_ids=request.assetIds,
            user=user,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
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
        updated = await update_asset(session, asset_id=asset_id, name=request.name, user=user)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
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
        updated = await update_delivery_basis(session, basis_id=basis_id, name=request.name, user=user)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
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
        await update_project_user(
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
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


async def _raw_materials_project_id(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект «Сырьё» не найден.")
    return project_id
