"""
Сервис чтения данных проекта «Сырьё» (read-only).

Запросы повторяют логику Tauri-приложения (domain.rs): строки форм
«Движение» и «Цены» с сортировкой по наименованию сырья и активу.
"""

from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select as sqlalchemy_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import Select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.schemas import AssetAccessRead
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    RawMaterialUserAsset,
)
from src.raw_materials.schemas import (
    AssetRowRead,
    CellUpdate,
    DeliveryBasisRowRead,
    MovementRowRead,
    PriceRowRead,
    RawMaterialRowRead,
    RawMaterialsMetaRead,
    UserRowRead,
)

# SQLModel-овские перегрузки select() поддерживают до 4 сущностей; для
# 5-сущностных запросов форм используем типизированный cast поверх SQLAlchemy.
MovementRowsSelect = Select[tuple[MaterialMovement, AssetRawMaterial, RawMaterialAsset, RawMaterial, RawMaterialPrice]]
PriceRowsSelect = Select[tuple[RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis]]


class NoRawMaterialsAccessError(Exception):
    """У пользователя нет доступа к проекту «Сырьё»."""


async def resolve_raw_materials_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[ProjectRole], list[RawMaterialAsset]]:
    """
    Возвращает (пользователь, роли, доступные активы) для проекта «Сырьё».

    Администратор проекта видит все активы; работник по активам — только
    привязанные к нему. Бросает NoRawMaterialsAccessError, если у пользователя
    нет роли в проекте.
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoRawMaterialsAccessError

    roles = list(
        (
            await session.exec(
                select(col(UserRole.role))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(
                    col(UserRole.user_id) == user.id,
                    col(Project.key) == RAW_MATERIALS_PROJECT_KEY,
                    col(UserRole.is_active).is_(True),
                )
            )
        ).all()
    )
    if not roles:
        raise NoRawMaterialsAccessError

    if ProjectRole.ADMIN in roles:
        assets = (await session.exec(select(RawMaterialAsset).order_by(RawMaterialAsset.name))).all()
    else:
        assets = (
            await session.exec(
                select(RawMaterialAsset)
                .join(RawMaterialUserAsset, col(RawMaterialUserAsset.asset_id) == col(RawMaterialAsset.id))
                .where(RawMaterialUserAsset.user_id == user.id)
                .order_by(RawMaterialAsset.name)
            )
        ).all()
    return user, roles, list(assets)


def accessible_assets_read(assets: list[RawMaterialAsset]) -> list[AssetAccessRead]:
    return [AssetAccessRead(name=asset.name) for asset in assets]


async def read_meta(session: AsyncSession, assets: list[RawMaterialAsset]) -> RawMaterialsMetaRead:
    periods = (
        await session.exec(select(col(MovementPeriod.report_month)).order_by(col(MovementPeriod.report_month)))
    ).all()
    bases = (await session.exec(select(DeliveryBasis.name).order_by(DeliveryBasis.name))).all()
    return RawMaterialsMetaRead(
        assets=[asset.name for asset in assets],
        movementPeriods=list(periods),
        deliveryBases=list(bases),
    )


async def read_movement_rows(
    session: AsyncSession,
    report_month: date,
    assets: list[RawMaterialAsset],
) -> list[MovementRowRead]:
    """Строки формы «Движение» за отчётный месяц (как read_current_movements в Tauri)."""
    query: MovementRowsSelect = cast(
        "MovementRowsSelect",
        sqlalchemy_select(
            MaterialMovement,
            AssetRawMaterial,
            RawMaterialAsset,
            RawMaterial,
            RawMaterialPrice,
        )
        .join(AssetRawMaterial, col(AssetRawMaterial.id) == col(MaterialMovement.asset_raw_material_id))
        .join(RawMaterialPrice, col(RawMaterialPrice.asset_raw_material_id) == col(AssetRawMaterial.id))
        .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .join(RawMaterial, col(RawMaterial.id) == col(AssetRawMaterial.raw_material_id))
        .where(col(MaterialMovement.report_month) == report_month)
        .order_by(RawMaterial.name, RawMaterialAsset.name, col(AssetRawMaterial.id)),
    )
    if assets:
        asset_ids = [asset.id for asset in assets]
        query = query.where(col(RawMaterialAsset.id).in_(asset_ids))

    rows = []
    for movement, arm, asset, raw_material, price in (await session.exec(query)).all():
        updated_at, updated_by = movement_audit(movement, price)
        rows.append(
            MovementRowRead(
                id=movement.id,
                key=arm.id,
                assetId=asset.id,
                assetName=asset.name,
                rawMaterialId=raw_material.id,
                rawMaterialName=raw_material.name,
                businessPlanTons=price.business_plan_quantity_tons,
                demandPlanTons=movement.demand_plan_tons,
                consumptionFactTons=movement.consumption_fact_tons,
                demandDeviationTons=calculate_deviation(movement.demand_plan_tons, movement.consumption_fact_tons),
                deliveryPlanTons=movement.delivery_plan_tons,
                deliveryFactTons=movement.delivery_fact_tons,
                deliveryDeviationTons=calculate_deviation(movement.delivery_plan_tons, movement.delivery_fact_tons),
                balanceStartTons=movement.balance_start_tons,
                balanceOnSiteTons=movement.balance_on_site_tons,
                balanceInTransitTons=movement.balance_in_transit_tons,
                balanceEndTons=movement.balance_end_tons,
                storageStandardTons=movement.storage_standard_tons,
                deviationReason=movement.deviation_reason,
                createdAt=arm.created_at,
                updatedAt=updated_at,
                updatedBy=updated_by,
                priceId=price.id,
            )
        )
    return rows


async def read_price_rows(
    session: AsyncSession,
    assets: list[RawMaterialAsset],
) -> list[PriceRowRead]:
    """Строки формы «Цены» (как read_current_prices в Tauri)."""
    query: PriceRowsSelect = cast(
        "PriceRowsSelect",
        sqlalchemy_select(RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis)
        .join(AssetRawMaterial, col(AssetRawMaterial.id) == col(RawMaterialPrice.asset_raw_material_id))
        .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .join(RawMaterial, col(RawMaterial.id) == col(AssetRawMaterial.raw_material_id))
        .join(DeliveryBasis, col(DeliveryBasis.id) == col(RawMaterialPrice.delivery_basis_id), isouter=True)
        .order_by(RawMaterial.name, RawMaterialAsset.name, col(AssetRawMaterial.id)),
    )
    if assets:
        asset_ids = [asset.id for asset in assets]
        query = query.where(col(RawMaterialAsset.id).in_(asset_ids))

    rows = []
    for price, arm, asset, raw_material, basis in (await session.exec(query)).all():
        rows.append(
            PriceRowRead(
                id=price.id,
                key=arm.id,
                assetId=asset.id,
                assetName=asset.name,
                rawMaterialId=raw_material.id,
                rawMaterialName=raw_material.name,
                deliveryBasisName=basis.name if basis else None,
                fact2025=price.fact_2025,
                businessPlanQuantityTons=price.business_plan_quantity_tons,
                businessPlanUsdPerTon=price.business_plan_usd_per_ton,
                businessPlanRubPerTon=price.business_plan_rub_per_ton,
                deliveryCostRub=price.delivery_cost_rub,
                createdAt=arm.created_at,
                updatedAt=price.updated_at,
                updatedBy=price.updated_by,
            )
        )
    return rows


async def read_raw_material_rows(
    session: AsyncSession,
    *,
    raw_material_id: UUID | None = None,
) -> list[RawMaterialRowRead]:
    """Строки справочника «Сырьевые позиции» с активами."""
    query = (
        select(RawMaterial, RawMaterialAsset, AssetRawMaterial)
        .outerjoin(AssetRawMaterial, col(AssetRawMaterial.raw_material_id) == col(RawMaterial.id))
        .outerjoin(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .order_by(RawMaterial.name, RawMaterialAsset.name)
    )
    if raw_material_id is not None:
        query = query.where(col(RawMaterial.id) == raw_material_id)
    rows = (await session.exec(query)).all()
    grouped: dict[UUID, tuple[RawMaterial, list[str]]] = {}
    for raw_material, asset, _ in rows:
        entry = grouped.setdefault(raw_material.id, (raw_material, []))
        if asset is not None:
            entry[1].append(asset.name)
    return [
        RawMaterialRowRead(
            id=raw_material.id,
            name=raw_material.name,
            assetNames=names,
            createdAt=raw_material.created_at,
            updatedAt=raw_material.updated_at,
            updatedBy=raw_material.updated_by,
        )
        for raw_material, names in (entry for _, entry in grouped.items())
    ]


async def read_asset_rows(assets: list[RawMaterialAsset]) -> list[AssetRowRead]:
    """Строки справочника «Активы» в пределах доступа пользователя."""
    return [
        AssetRowRead(
            id=asset.id,
            name=asset.name,
            createdAt=asset.created_at,
            updatedAt=asset.updated_at,
            updatedBy=asset.updated_by,
        )
        for asset in assets
    ]


async def read_delivery_basis_rows(
    session: AsyncSession,
    *,
    delivery_basis_id: UUID | None = None,
) -> list[DeliveryBasisRowRead]:
    """Строки справочника «Базисы поставки»."""
    query = select(DeliveryBasis).order_by(DeliveryBasis.name)
    if delivery_basis_id is not None:
        query = query.where(col(DeliveryBasis.id) == delivery_basis_id)
    bases = (await session.exec(query)).all()
    return [
        DeliveryBasisRowRead(
            id=basis.id,
            name=basis.name,
            createdAt=basis.created_at,
            updatedAt=basis.updated_at,
            updatedBy=basis.updated_by,
        )
        for basis in bases
    ]


async def read_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
) -> list[UserRowRead]:
    """
    Строки справочника «Пользователи» (проект «Сырьё», без удалённых).

    Показываются все пользователи, у которых есть связь с проектом (включая
    неактивных — их активирует администратор проекта). isActive и isAdmin
    берутся из пер-проектной связи, а не из глобальных полей пользователя.
    """
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).first()
    if project_id is None:
        return []
    query = (
        select(User, UserRole)
        .join(UserRole, col(UserRole.user_id) == col(User.id))
        .where(col(UserRole.project_id) == project_id, col(User.is_deleted) == False)  # noqa: E712
        .order_by(User.name)
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    rows = (await session.exec(query)).all()
    users = [user for user, _ in rows]
    links_by_user = {user.id: link for user, link in rows}
    asset_links = (
        await session.exec(
            select(RawMaterialUserAsset, RawMaterialAsset)
            .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(RawMaterialUserAsset.asset_id))
            .where(col(RawMaterialUserAsset.user_id).in_([user.id for user in users]))
            .order_by(RawMaterialAsset.name)
        )
    ).all()
    names_by_user: dict[UUID, list[str]] = {}
    for link, asset in asset_links:
        names_by_user.setdefault(link.user_id, []).append(asset.name)
    return [
        UserRowRead(
            id=user.id,
            email=user.email,
            fullname=user.name or "",
            isAdmin=link.role == ProjectRole.ADMIN,
            assetNames=names_by_user.get(user.id, []),
            isActive=link.is_active,
            createdAt=user.created_at,
            updatedAt=link.updated_at,
            updatedBy=link.updated_by,
        )
        for user, link in ((user, links_by_user[user.id]) for user in users)
    ]


def movement_audit(
    movement: MaterialMovement,
    price: RawMaterialPrice,
) -> tuple[datetime, str]:
    """
    Аудит строки «Движения»: берётся более свежая правка из движения или цен.

    В SQLite это CASE-сравнение строк; в Postgres business_plan_updated_at —
    TEXT (может содержать заглушку '<УГАДАЙТЕ>'), поэтому невалидные значения
    трактуются как «не обновлялось» и выбирается аудит движения.
    """
    plan_updated_at = parse_datetime_or_none(price.business_plan_updated_at)
    if plan_updated_at is not None and plan_updated_at > movement.updated_at:
        return plan_updated_at, price.business_plan_updated_by
    return movement.updated_at, movement.updated_by


def parse_datetime_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def calculate_deviation(plan: float | None, fact: float | None) -> float | None:
    if plan is None or fact is None:
        return None
    return plan - fact


# ─── Запись: правки ячеек форм «Движение» и «Цены» ────────────────

# Поля формы «Движение», которые пишутся в MaterialMovement.
# Типовые сообщения об ошибках записи (модульные константы — стиль проекта).
_CELL_MISSING_ROW_OR_FIELD = ValueError("Ячейка не содержит идентификатор строки или поля.")
_CELL_MOVEMENT_NOT_FOUND = ValueError("Строка «Движения» не найдена.")
_CELL_MOVEMENT_NO_ACCESS = ValueError("Нет доступа к строке «Движения».")
_CELL_MOVEMENT_NO_PRICE = ValueError("Для строки «Движения» не найдена цена.")
_CELL_PRICE_NOT_FOUND = ValueError("Строка «Цен» не найдена.")
_CELL_PRICE_NO_ACCESS = ValueError("Нет доступа к строке «Цен».")
_DICT_REF_RAW_MATERIAL_NOT_FOUND = ValueError("Сырьевая позиция не найдена.")
_DICT_REF_RAW_MATERIAL_USED = ValueError("Нельзя удалить: позиция используется в формах «Движение» или «Цены».")
_DICT_REF_ASSET_NOT_FOUND = ValueError("Актив не найден.")
_DICT_REF_ASSET_USED = ValueError("Нельзя удалить: актив используется в формах «Движение» или «Цены».")
_DICT_REF_BASIS_NOT_FOUND = ValueError("Базис поставки не найден.")
_DICT_REF_BASIS_USED = ValueError("Нельзя удалить: базис используется в форме «Цены».")
_DICT_REF_USER_EXISTS = ValueError("Пользователь с таким email уже существует.")
_DICT_REF_USER_NOT_FOUND = ValueError("Пользователь не найден.")
_DICT_REF_ASSETS_NOT_FOUND = ValueError("Один или несколько активов не найдены.")

MOVEMENT_CELL_FIELDS: dict[str, str] = {
    "demandPlanTons": "demand_plan_tons",
    "consumptionFactTons": "consumption_fact_tons",
    "deliveryPlanTons": "delivery_plan_tons",
    "deliveryFactTons": "delivery_fact_tons",
    "balanceStartTons": "balance_start_tons",
    "balanceOnSiteTons": "balance_on_site_tons",
    "balanceInTransitTons": "balance_in_transit_tons",
    "balanceEndTons": "balance_end_tons",
    "storageStandardTons": "storage_standard_tons",
    "deviationReason": "deviation_reason",
}

# Поля формы «Движение», которые пишутся в RawMaterialPrice (бизнес-план).
MOVEMENT_PRICE_FIELDS: dict[str, str] = {
    "businessPlanTons": "business_plan_quantity_tons",
}

# Поля формы «Цены», которые пишутся в RawMaterialPrice.
PRICE_CELL_FIELDS: dict[str, str] = {
    "fact2025": "fact_2025",
    "businessPlanQuantityTons": "business_plan_quantity_tons",
    "businessPlanUsdPerTon": "business_plan_usd_per_ton",
    "businessPlanRubPerTon": "business_plan_rub_per_ton",
    "deliveryCostRub": "delivery_cost_rub",
}


class CellValidationError(ValueError):
    """Невалидная ячейка (имя поля или значение)."""


async def update_movement_cells(
    session: AsyncSession,
    *,
    report_month: date,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
) -> int:
    """Применяет правки ячеек формы «Движение» за отчётный месяц."""
    asset_ids = [asset.id for asset in assets]
    updated = 0
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        movement = await _movement_row(session, row_id, report_month, asset_ids)
        if field in MOVEMENT_CELL_FIELDS:
            setattr(movement, MOVEMENT_CELL_FIELDS[field], coerce_number_or_text(value, field))
        elif field in MOVEMENT_PRICE_FIELDS:
            price = await _movement_price(session, movement)
            setattr(price, MOVEMENT_PRICE_FIELDS[field], coerce_number_or_text(value, field))
            price.business_plan_updated_at = datetime.now(UTC).isoformat()
            price.business_plan_updated_by = user.name or user.email or ""
            session.add(price)
        else:
            message = f"Неизвестное поле «{field}» формы «Движение»."
            raise CellValidationError(message)
        movement.updated_at = datetime.now(UTC)
        movement.updated_by = user.name or user.email or ""
        session.add(movement)
        updated += 1
    await session.commit()
    return updated


async def update_price_cells(
    session: AsyncSession,
    *,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
) -> int:
    """Применяет правки ячеек формы «Цены»."""
    asset_ids = [asset.id for asset in assets]
    updated = 0
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        price = await _price_row(session, row_id, asset_ids)
        if field == "deliveryBasisName":
            price.delivery_basis_id = await resolve_basis_id(session, coerce_text(value))
        elif field in PRICE_CELL_FIELDS:
            setattr(price, PRICE_CELL_FIELDS[field], coerce_number_or_text(value, field))
        else:
            message = f"Неизвестное поле «{field}» формы «Цены»."
            raise CellValidationError(message)
        price.updated_at = datetime.now(UTC)
        price.updated_by = user.name or user.email or ""
        session.add(price)
        updated += 1
    await session.commit()
    return updated


async def _movement_row(
    session: AsyncSession,
    row_id: UUID,
    report_month: date,
    asset_ids: list[UUID],
) -> MaterialMovement:
    movement = await session.get(MaterialMovement, row_id)
    if movement is None or movement.report_month != report_month:
        raise _CELL_MOVEMENT_NOT_FOUND
    if asset_ids and not await _arm_belongs_to_assets(session, movement.asset_raw_material_id, asset_ids):
        raise _CELL_MOVEMENT_NO_ACCESS
    return movement


async def _movement_price(session: AsyncSession, movement: MaterialMovement) -> RawMaterialPrice:
    price = (
        await session.exec(
            select(RawMaterialPrice).where(
                col(RawMaterialPrice.asset_raw_material_id) == movement.asset_raw_material_id
            )
        )
    ).one_or_none()
    if price is None:
        raise _CELL_MOVEMENT_NO_PRICE
    return price


async def _price_row(session: AsyncSession, row_id: UUID, asset_ids: list[UUID]) -> RawMaterialPrice:
    price = await session.get(RawMaterialPrice, row_id)
    if price is None:
        raise _CELL_PRICE_NOT_FOUND
    if asset_ids and not await _arm_belongs_to_assets(session, price.asset_raw_material_id, asset_ids):
        raise _CELL_PRICE_NO_ACCESS
    return price


async def _arm_belongs_to_assets(session: AsyncSession, arm_id: UUID, asset_ids: list[UUID]) -> bool:
    """Связка актив-сырьё относится к одному из активов пользователя."""
    arm = await session.get(AssetRawMaterial, arm_id)
    return arm is not None and arm.asset_id in asset_ids


def coerce_number_or_text(value: object, field: str) -> float | str | None:
    """Число из ячейки ('' / None / '1 234,5' → число или текст)."""
    if field == "deviationReason":
        return coerce_text(value)
    return coerce_number(value)


def coerce_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        message = f"Не число: {value!r}"
        raise CellValidationError(message) from error


def coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def resolve_basis_id(session: AsyncSession, name: str | None) -> UUID | None:
    """Базис поставки по имени (''/None → без базиса)."""
    if not name:
        return None
    basis = (await session.exec(select(DeliveryBasis).where(col(DeliveryBasis.name) == name))).one_or_none()
    if basis is None:
        message = f"Базис поставки «{name}» не найден."
        raise CellValidationError(message)
    return basis.id


# ─── Запись: справочники ──────────────────────────────────────────


class DictionaryReferenceError(ValueError):
    """Нарушение ссылочной целостности при изменении справочника."""


async def create_raw_material(
    session: AsyncSession,
    *,
    name: str,
    asset_ids: list[UUID],
    user: User,
) -> RawMaterial:
    raw_material = RawMaterial(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=user.name or user.email or "",
    )
    session.add(raw_material)
    await session.flush()
    await replace_asset_links(session, raw_material.id, asset_ids)
    await session.commit()
    return raw_material


async def update_raw_material(
    session: AsyncSession,
    *,
    raw_material_id: UUID,
    name: str | None,
    asset_ids: list[UUID] | None,
    user: User,
) -> RawMaterial:
    raw_material = await session.get(RawMaterial, raw_material_id)
    if raw_material is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    if name is not None:
        raw_material.name = name.strip()
    if asset_ids is not None:
        await replace_asset_links(session, raw_material.id, asset_ids)
    raw_material.updated_at = datetime.now(UTC)
    raw_material.updated_by = user.name or user.email or ""
    session.add(raw_material)
    await session.commit()
    return raw_material


async def delete_raw_material(session: AsyncSession, *, raw_material_id: UUID) -> None:
    raw_material = await session.get(RawMaterial, raw_material_id)
    if raw_material is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    links = (
        await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
    ).all()
    for link in links:
        if await _arm_is_used(session, link.id):
            raise _DICT_REF_RAW_MATERIAL_USED
        await session.delete(link)
    await session.delete(raw_material)
    await session.commit()


async def create_asset(session: AsyncSession, *, name: str, user: User) -> RawMaterialAsset:
    asset = RawMaterialAsset(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=user.name or user.email or "",
    )
    session.add(asset)
    await session.commit()
    return asset


async def update_asset(session: AsyncSession, *, asset_id: UUID, name: str | None, user: User) -> RawMaterialAsset:
    asset = await session.get(RawMaterialAsset, asset_id)
    if asset is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    if name is not None:
        asset.name = name.strip()
    asset.updated_at = datetime.now(UTC)
    asset.updated_by = user.name or user.email or ""
    session.add(asset)
    await session.commit()
    return asset


async def delete_asset(session: AsyncSession, *, asset_id: UUID) -> None:
    asset = await session.get(RawMaterialAsset, asset_id)
    if asset is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    links = (await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.asset_id) == asset_id))).all()
    for link in links:
        if await _arm_is_used(session, link.id):
            raise _DICT_REF_ASSET_USED
        await session.delete(link)
    user_links = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.asset_id) == asset_id))
    ).all()
    for link in user_links:
        await session.delete(link)
    await session.delete(asset)
    await session.commit()


async def create_delivery_basis(session: AsyncSession, *, name: str, user: User) -> DeliveryBasis:
    basis = DeliveryBasis(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=user.name or user.email or "",
    )
    session.add(basis)
    await session.commit()
    return basis


async def update_delivery_basis(
    session: AsyncSession,
    *,
    basis_id: UUID,
    name: str | None,
    user: User,
) -> DeliveryBasis:
    basis = await session.get(DeliveryBasis, basis_id)
    if basis is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    if name is not None:
        basis.name = name.strip()
    basis.updated_at = datetime.now(UTC)
    basis.updated_by = user.name or user.email or ""
    session.add(basis)
    await session.commit()
    return basis


async def delete_delivery_basis(session: AsyncSession, *, basis_id: UUID) -> None:
    basis = await session.get(DeliveryBasis, basis_id)
    if basis is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    price = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.delivery_basis_id) == basis_id))
    ).one_or_none()
    if price is not None:
        raise _DICT_REF_BASIS_USED
    await session.delete(basis)
    await session.commit()


async def create_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    is_admin: bool,
    asset_names: list[str],
) -> User:
    """Создаёт локального пользователя проекта «Сырьё» с ролью и активами."""
    email = email.strip()
    existing = (await session.exec(select(User).where(col(User.email) == email))).one_or_none()
    if existing is not None:
        message = f"Пользователь с email {email} уже существует."
        raise DictionaryReferenceError(message)
    now = datetime.now(UTC)
    user = User(
        key=email,
        email=email,
        name=fullname.strip(),
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    session.add(
        UserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    await replace_user_assets(session, user.id, asset_names)
    await session.commit()
    return user


async def update_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    email: str | None,
    fullname: str | None,
    is_admin: bool | None,
    asset_names: list[str] | None,
    is_active: bool | None,
    reset_password: bool,
    updated_by: str = "",
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_REF_USER_NOT_FOUND
    link = (
        await session.exec(select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_REF_USER_NOT_FOUND
    if email is not None:
        candidate = email.strip()
        other = (await session.exec(select(User).where(col(User.email) == candidate))).one_or_none()
        if other is not None and other.id != user.id:
            message = f"Пользователь с email {candidate} уже существует."
            raise DictionaryReferenceError(message)
        user.email = candidate
        user.key = candidate
    if fullname is not None:
        user.name = fullname.strip()
    if is_active is not None:
        link.is_active = is_active
    if reset_password:
        user.password_hash = None
    role_changed = is_admin is not None and (link.role == ProjectRole.ADMIN) != is_admin
    if is_admin is not None:
        link.role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    if asset_names is not None or role_changed:
        await replace_user_assets(
            session,
            user.id,
            asset_names if asset_names is not None else ([] if is_admin else None),
        )
    link.updated_at = datetime.now(UTC)
    link.updated_by = updated_by
    user.updated_at = datetime.now(UTC)
    session.add(user)
    session.add(link)
    await session.commit()
    return user


async def delete_project_user(session: AsyncSession, *, project_id: UUID, user_id: UUID) -> None:
    """
    Удаляет пользователя из проекта: снимает связь с проектом и активы.

    Учётная запись пользователя (и его связи с другими проектами) не
    затрагивается — удаление действует только на текущий проект.
    """
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_REF_USER_NOT_FOUND
    links = (
        await session.exec(select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id == project_id))
    ).all()
    for link in links:
        await session.delete(link)
    project_assets = (
        await session.exec(
            select(RawMaterialUserAsset)
            .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(RawMaterialUserAsset.asset_id))
            .where(col(RawMaterialUserAsset.user_id) == user_id)
        )
    ).all()
    for link in project_assets:
        await session.delete(link)
    await session.commit()


async def replace_asset_links(session: AsyncSession, raw_material_id: UUID, asset_ids: list[UUID]) -> None:
    """Заменяет связки актив-сырьё для сырьевой позиции."""
    existing = (
        await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
    ).all()
    for link in existing:
        await session.delete(link)
    now = datetime.now(UTC)
    for asset_id in asset_ids:
        session.add(
            AssetRawMaterial(
                asset_id=asset_id,
                raw_material_id=raw_material_id,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()


async def replace_user_assets(session: AsyncSession, user_id: UUID, asset_names: list[str] | None) -> None:
    """Заменяет привязку пользователя к активам (None — только удалить связи админа)."""
    existing = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.user_id) == user_id))
    ).all()
    for link in existing:
        await session.delete(link)
    if asset_names:
        assets = (await session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).in_(asset_names)))).all()
        names_found = {asset.name for asset in assets}
        missing = [name for name in asset_names if name not in names_found]
        if missing:
            message = f"Активы не найдены: {', '.join(missing)}"
            raise DictionaryReferenceError(message)
        now = datetime.now(UTC)
        for asset in assets:
            session.add(RawMaterialUserAsset(user_id=user_id, asset_id=asset.id, created_at=now))
    await session.flush()


async def _arm_is_used(session: AsyncSession, arm_id: UUID) -> bool:
    movement = (
        await session.exec(select(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id) == arm_id))
    ).one_or_none()
    if movement is not None:
        return True
    price = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id) == arm_id))
    ).one_or_none()
    return price is not None
