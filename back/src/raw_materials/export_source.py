"""Bounded exports using the same projections and identities as the raw-material forms."""

from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, User, UserRole
from src.common.export import safe_sheet_name
from src.common.query_exports import QueryExportSpec, TableSource

from .dictionaries import read_asset_rows, read_delivery_basis_rows, read_raw_material_rows
from .export import CALCULATED_OPERANDS, REFERENCE_LISTS, SECTION_SHEET_NAMES
from .models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    ReportSnapshot,
    SnapshotMaterialMovement,
)
from .schemas import ExportViewRequest
from .service import read_movement_rows, read_price_rows, read_snapshot_export_rows
from .users import read_user_rows


async def resolve_export_source(session: AsyncSession, spec: QueryExportSpec) -> TableSource:
    request = ExportViewRequest.model_validate(spec.request)
    # The authenticated route captures this scope. Empty never means all assets.
    assets = list(
        (
            await session.exec(
                select(RawMaterialAsset)
                .where(col(RawMaterialAsset.id).in_(spec.asset_ids))
                .order_by(col(RawMaterialAsset.name))
            )
        ).all()
    )
    section = request.section
    if section == "materialMovement" and request.snapshotId is not None:
        if await session.get(ReportSnapshot, request.snapshotId) is None:
            raise HTTPException(status_code=404, detail="Снимок не найден.")
        ids = (
            select(SnapshotMaterialMovement.source_asset_raw_material_id)
            .where(
                col(SnapshotMaterialMovement.snapshot_id) == request.snapshotId,
                col(SnapshotMaterialMovement.source_asset_id).in_(spec.asset_ids),
            )
            .order_by(
                col(SnapshotMaterialMovement.raw_material_name),
                col(SnapshotMaterialMovement.asset_name),
                col(SnapshotMaterialMovement.source_asset_raw_material_id),
            )
        )
    elif section in {"materialMovement", "rawMaterialPrices"}:
        model = MaterialMovement if section == "materialMovement" else RawMaterialPrice
        ids = (
            select(model.id)
            .join(AssetRawMaterial, col(AssetRawMaterial.id) == col(model.asset_raw_material_id))
            .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
            .join(RawMaterial, col(RawMaterial.id) == col(AssetRawMaterial.raw_material_id))
            .where(col(RawMaterialAsset.id).in_(spec.asset_ids))
            .order_by(col(RawMaterial.name), col(RawMaterialAsset.name), col(AssetRawMaterial.id))
        )
        if section == "materialMovement":
            if request.reportMonth is None:
                request.reportMonth = await session.scalar(select(func.max(MovementPeriod.report_month)))
            if request.reportMonth is None:
                raise HTTPException(status_code=404, detail="Нет периодов движения.")
            # The projection requires prices for business-plan and storage values.
            ids = ids.join(RawMaterialPrice, col(RawMaterialPrice.asset_raw_material_id) == col(AssetRawMaterial.id))
            ids = ids.where(col(MaterialMovement.report_month) == request.reportMonth)
    elif section == "rawMaterials":
        ids = select(RawMaterial.id).order_by(col(RawMaterial.name), col(RawMaterial.id))
    elif section == "assets":
        ids = select(RawMaterialAsset.id).where(col(RawMaterialAsset.id).in_(spec.asset_ids))
        ids = ids.order_by(col(RawMaterialAsset.name), col(RawMaterialAsset.id))
    elif section == "deliveryBases":
        ids = select(DeliveryBasis.id).order_by(col(DeliveryBasis.name), col(DeliveryBasis.id))
    else:
        ids = (
            select(User.id)
            .join(UserRole, col(UserRole.user_id) == col(User.id))
            .join(Project, col(Project.id) == col(UserRole.project_id))
            .where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY, col(User.is_deleted).is_(False))
            .order_by(col(UserRole.is_active).desc(), func.coalesce(col(User.name), ""), col(User.id))
        )
    spec.request = request.model_dump(mode="json")  # pin the resolved month before enqueue

    async def load(row_ids: list[UUID]) -> Sequence[BaseModel]:
        if section == "materialMovement":
            if request.snapshotId is not None:
                _, rows = await read_snapshot_export_rows(session, request.snapshotId, assets, row_ids=row_ids)
                return rows
            assert request.reportMonth is not None  # noqa: S101
            return await read_movement_rows(session, request.reportMonth, assets, row_ids=row_ids)
        if section == "rawMaterialPrices":
            return await read_price_rows(session, assets, row_ids=row_ids)
        if section == "rawMaterials":
            return await read_raw_material_rows(session, row_ids=row_ids)
        if section == "assets":
            selected = set(row_ids)
            return await read_asset_rows([asset for asset in assets if asset.id in selected])
        if section == "deliveryBases":
            return await read_delivery_basis_rows(session, row_ids=row_ids)
        return await read_user_rows(session, row_ids=row_ids)

    lists = None
    if any(column.key in REFERENCE_LISTS for column in request.columns):
        bases = (await session.exec(select(DeliveryBasis.name).order_by(DeliveryBasis.name))).all()
        lists = {"assets": [asset.name for asset in assets], "deliveryBases": list(bases)}
    source = TableSource(
        ids=ids,
        load=load,
        title=safe_sheet_name(SECTION_SHEET_NAMES[section]),
        columns=request.columns,
        header_pinned=request.headerPinned,
        lists=lists,
        column_lists=REFERENCE_LISTS,
        calculated=CALCULATED_OPERANDS,
    )
    source.select_rows(request.rowIds)
    return source
