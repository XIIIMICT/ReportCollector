"""
Утверждение («фиксация») формы «Движение» за отчётный месяц.

Поведение повторяет ReportCollector-RawMaterials (fix_report_for_instance):
- фиксировать можно только первый открытый месяц, только когда все строки
  заполнены и ячейки не редактируются другими пользователями;
- предварительная версия перезаписывается, финальная — неизменна;
- финальная фиксация закрывает период и открывает следующий месяц;
- при фиксации очищается вся история undo/redo и состояния ячеек.
"""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.collab.models import MOVEMENT_TABLE
from src.collab.store import lease_key
from src.raw_materials.models import (
    AssetRawMaterial,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    ReportSnapshot,
    SnapshotMaterialMovement,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from src.auth.models import User
    from src.collab.store import CollabStore

COMPLETENESS_FIELDS = (
    "demand_plan_tons",
    "consumption_fact_tons",
    "delivery_plan_tons",
    "delivery_fact_tons",
    "balance_start_tons",
    "balance_on_site_tons",
    "balance_in_transit_tons",
    "balance_end_tons",
    "storage_standard_tons",
)
"""Поля «Движения», обязательные для фиксации (без deviation_reason)."""


class ApprovalError(Exception):
    """Бизнес-ошибка фиксации с понятным сообщением для пользователя."""


ERR_FIRST_DAY = "Отчётный месяц должен начинаться с первого числа."
ERR_FINAL_OVERWRITE = "Финальную фиксацию нельзя перезаписать."
ERR_NO_PERIODS = "Нет открытых отчётных периодов."
ERR_NOT_FIRST_MONTH = "Зафиксировать можно только первый открытый отчётный месяц."
ERR_NO_ROWS = "Нет строк движения за отчётный месяц."
ERR_LEASED = "Фиксация временно недоступна: другой пользователь сейчас редактирует ячейку ({name})."
ERR_NEXT_MONTH = "Не удалось открыть следующий отчётный месяц: нет открытых периодов."


def _movement_row_complete(row: MaterialMovement, price: RawMaterialPrice | None) -> bool:
    if price is None or price.business_plan_quantity_tons is None:
        return False
    if not (row.deviation_reason or "").strip():
        return False
    return all(getattr(row, field) is not None for field in COMPLETENESS_FIELDS)


def _month_rows_complete(rows: list[MaterialMovement], prices: dict[UUID, RawMaterialPrice]) -> int:
    """Число незаполненных строк месяца (0 — месяц готов к фиксации)."""
    return sum(1 for row in rows if not _movement_row_complete(row, prices.get(row.asset_raw_material_id)))


def _next_month(report_month: date) -> date:
    if report_month.month == 12:
        return date(report_month.year + 1, 1, 1)
    return date(report_month.year, report_month.month + 1, 1)


async def fix_report(
    session: AsyncSession,
    *,
    user: User,
    report_month: date,
    store: CollabStore,
) -> ReportSnapshot:
    """Фиксирует «Движение» за месяц: создаёт снимок, при финале открывает следующий месяц."""
    if report_month.day != 1:
        raise ApprovalError(ERR_FIRST_DAY)
    existing = (
        await session.exec(select(ReportSnapshot).where(col(ReportSnapshot.report_month) == report_month))
    ).first()
    if existing is not None and existing.is_final:
        raise ApprovalError(ERR_FINAL_OVERWRITE)
    previous_snapshot_id = existing.id if existing is not None else None
    first_open = (
        await session.exec(select(col(MovementPeriod.report_month)).order_by(col(MovementPeriod.report_month)))
    ).first()
    if first_open is None:
        raise ApprovalError(ERR_NO_PERIODS)
    if report_month != first_open:
        raise ApprovalError(ERR_NOT_FIRST_MONTH)
    rows = list(
        (await session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == report_month))).all()
    )
    if not rows:
        raise ApprovalError(ERR_NO_ROWS)
    prices = {price.asset_raw_material_id: price for price in (await session.exec(select(RawMaterialPrice))).all()}
    incomplete = _month_rows_complete(rows, prices)
    if incomplete:
        message = (
            f"Фиксация невозможна: незаполненных строк — {incomplete} из {len(rows)}. "
            "Заполните все поля движения сырья."
        )
        raise ApprovalError(message)

    lease_keys = [
        lease_key(f"{MOVEMENT_TABLE}/{row.id}/{field}", project=RAW_MATERIALS_PROJECT_KEY)
        for row in rows
        for field in (*COMPLETENESS_FIELDS, "deviation_reason")
    ]
    lease_owner = await store.any_active_lease(lease_keys)
    if lease_owner is not None:
        raise ApprovalError(ERR_LEASED.format(name=lease_owner.get("name") or "неизвестный пользователь"))

    now = datetime.now(UTC)
    is_final = report_month < datetime.now().astimezone().date().replace(day=1)
    for snapshot in (
        await session.exec(select(ReportSnapshot).where(col(ReportSnapshot.report_month) == report_month))
    ).all():
        await session.delete(snapshot)
    await session.flush()

    snapshot = ReportSnapshot(
        report_month=report_month,
        fixed_at=now,
        fixed_by_user_id=user.id,
        fixed_by_email=user.email or "",
        fixed_by_fullname=user.name or user.email or "",
        is_final=is_final,
        previous_snapshot_id=previous_snapshot_id,
    )
    session.add(snapshot)
    await session.flush()

    arms = {arm.id: arm for arm in (await session.exec(select(AssetRawMaterial))).all()}
    asset_names = {asset.id: asset.name for asset in (await session.exec(select(RawMaterialAsset))).all()}
    material_names = {material.id: material.name for material in (await session.exec(select(RawMaterial))).all()}
    for row in rows:
        arm = arms[row.asset_raw_material_id]
        price = prices[row.asset_raw_material_id]
        demand_deviation = (
            None
            if row.demand_plan_tons is None or row.consumption_fact_tons is None
            else row.demand_plan_tons - row.consumption_fact_tons
        )
        delivery_deviation = (
            None
            if row.delivery_plan_tons is None or row.delivery_fact_tons is None
            else row.delivery_plan_tons - row.delivery_fact_tons
        )
        session.add(
            SnapshotMaterialMovement(
                snapshot_id=snapshot.id,
                source_asset_raw_material_id=arm.id,
                source_row_id=row.id,
                source_asset_id=arm.asset_id,
                source_raw_material_id=arm.raw_material_id,
                asset_name=asset_names[arm.asset_id],
                raw_material_name=material_names[arm.raw_material_id],
                business_plan_tons=price.business_plan_quantity_tons,
                demand_plan_tons=row.demand_plan_tons,
                consumption_fact_tons=row.consumption_fact_tons,
                demand_deviation_tons=demand_deviation,
                delivery_plan_tons=row.delivery_plan_tons,
                delivery_fact_tons=row.delivery_fact_tons,
                delivery_deviation_tons=delivery_deviation,
                balance_start_tons=row.balance_start_tons,
                balance_on_site_tons=row.balance_on_site_tons,
                balance_in_transit_tons=row.balance_in_transit_tons,
                balance_end_tons=row.balance_end_tons,
                storage_standard_tons=row.storage_standard_tons,
                deviation_reason=row.deviation_reason,
            )
        )

    if is_final:
        period = await session.get(MovementPeriod, report_month)
        if period is not None:
            await session.delete(period)
            await session.flush()
        latest_open = (
            await session.exec(
                select(col(MovementPeriod.report_month)).order_by(col(MovementPeriod.report_month).desc())
            )
        ).first()
        if latest_open is None:
            raise ApprovalError(ERR_NEXT_MONTH)
        next_month = _next_month(latest_open)
        session.add(MovementPeriod(report_month=next_month, created_at=now))
        await session.flush()
        for arm in arms.values():
            session.add(
                MaterialMovement(
                    asset_raw_material_id=arm.id,
                    report_month=next_month,
                    created_at=now,
                    updated_at=now,
                )
            )

    # Фиксация обнуляет историю правок: снимок — единственная точка отсчёта.
    await store.clear_all_undo_state()
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def read_movement_meta(session: AsyncSession, *, report_month: date) -> tuple[bool, bool, list[ReportSnapshot]]:
    """Статус месяца: полнота, финальность последнего снимка и архив версий."""
    rows = list(
        (await session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == report_month))).all()
    )
    prices = {price.asset_raw_material_id: price for price in (await session.exec(select(RawMaterialPrice))).all()}
    is_complete = bool(rows) and _month_rows_complete(rows, prices) == 0
    snapshots = list(
        (
            await session.exec(
                select(ReportSnapshot)
                .where(col(ReportSnapshot.report_month) == report_month)
                .order_by(col(ReportSnapshot.fixed_at).desc())
            )
        ).all()
    )
    is_final = bool(snapshots) and snapshots[0].is_final
    return is_complete, is_final, snapshots
