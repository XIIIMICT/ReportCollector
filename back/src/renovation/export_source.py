"""Dictionary exports with bounded domain projections and SQL summary totals."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.common.export import safe_sheet_name
from src.common.query_exports import QueryExportSpec, TableSource

from .assets import read_renovation_asset_rows
from .catalogs import read_renovation_catalog_rows
from .constants import RENOVATION_PROJECT_KEY
from .export import REFERENCE_LISTS, section_sheet_name, validate_export_request
from .models import RAsset, RUserRole
from .plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from .schemas import RenovationExportViewRequest
from .users import read_renovation_user_rows


async def resolve_export_source(session: AsyncSession, spec: QueryExportSpec) -> TableSource:
    request = RenovationExportViewRequest.model_validate(spec.request)
    validate_export_request(request)
    if request.section == "renovationAssets":
        ids = select(RAsset.id).order_by(col(RAsset.name), col(RAsset.id))
    elif request.section == "renovationUsers":
        ids = (
            select(User.id)
            .join(RUserRole, col(RUserRole.user_id) == col(User.id))
            .join(Project, col(Project.id) == col(RUserRole.project_id))
            .where(col(Project.key) == RENOVATION_PROJECT_KEY, col(User.is_deleted).is_(False))
            .order_by(col(RUserRole.is_active).desc(), func.coalesce(col(User.name), ""), col(User.id))
        )
    else:
        ids = (
            select(RenovationReferenceValue.id)
            .where(col(RenovationReferenceValue.catalog) == request.section.removeprefix("renovationCatalog:"))
            .order_by(col(RenovationReferenceValue.label), col(RenovationReferenceValue.id))
        )

    async def load(row_ids: list[UUID]) -> Sequence[BaseModel]:
        if request.section == "renovationAssets":
            return await read_renovation_asset_rows(session, row_ids=row_ids)
        if request.section == "renovationUsers":
            return await read_renovation_user_rows(session, row_ids=row_ids)
        return await read_renovation_catalog_rows(
            session, catalog=request.section.removeprefix("renovationCatalog:"), row_ids=row_ids
        )

    lists = None
    if any(column.key in REFERENCE_LISTS for column in request.columns):
        lists = {"assets": list((await session.exec(select(RAsset.name).order_by(RAsset.name))).all())}
    source = TableSource(
        ids=ids,
        load=load,
        title=safe_sheet_name(section_sheet_name(request.section)),
        columns=request.columns,
        header_pinned=request.headerPinned,
        lists=lists,
        column_lists=REFERENCE_LISTS,
    )
    source.select_rows(request.rowIds)
    keys = {column.key for column in request.columns} & {"repairCount", "totalCost"}
    if request.section == "renovationAssets" and keys:
        count, cost = (
            await session.exec(
                select(func.count(), func.coalesce(func.sum(RenovationPlanRow.col_031), 0))
                .select_from(RenovationPlanRow)
                .join(RenovationPlan, col(RenovationPlan.id) == col(RenovationPlanRow.plan_id))
                .where(
                    col(RenovationPlan.state) == RPlanState.WORKING,
                    col(RenovationPlanRow.asset_id).in_(source.ids.order_by(None)),
                )
            )
        ).one()
        totals = {"repairCount": float(count or 0), "totalCost": float(cost or 0)}
        source.totals = {key: totals[key] for key in keys}
    return source
