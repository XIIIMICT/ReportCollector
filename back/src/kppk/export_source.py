"""Bounded KPPK export queries with database ordering and batch projections."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User, UserRole
from src.common.export import safe_sheet_name, validate_columns_allowed, validate_export_shape
from src.common.query_exports import QueryExportSpec, TableSource
from src.kppk.constants import KPPK_PROJECT_KEY, market_order_sql
from src.kppk.core import KppkNotFoundError
from src.kppk.dictionaries import read_org_names, read_org_rows, read_product_position_rows, read_raw_rows
from src.kppk.export import (
    COST_KEYS,
    REALIZATION_MERGE_BY,
    REFERENCE_LISTS,
    SECTION_SHEET_NAMES,
    validate_export_request,
    validate_realization_export,
)
from src.kppk.models import (
    KppkCost,
    KppkMarket,
    KppkOrg,
    KppkOrgProduct,
    KppkProduct,
    KppkRaw,
    KppkRealization,
    KppkRealizationPlan,
)
from src.kppk.realization import (
    MAX_PRODUCT_NUMBER,
    _cost_row_read,
    _fact_realization_row_read,
    _plan_realization_row_read,
    read_current_period,
)
from src.kppk.schemas import KppkExportViewRequest, KppkRealizationExportRequest
from src.kppk.users import read_kppk_user_rows


async def resolve_export_source(session: AsyncSession, spec: QueryExportSpec) -> TableSource:
    if spec.source == "kppk_dictionary":
        dictionary_request = KppkExportViewRequest.model_validate(spec.request)
        validate_export_request(dictionary_request)

        if dictionary_request.section == "kppkPositions":
            ids = (
                select(KppkOrgProduct.id)
                .join(KppkProduct, col(KppkProduct.id) == col(KppkOrgProduct.product_id))
                .join(KppkOrg, col(KppkOrg.id) == col(KppkOrgProduct.org_id))
                .order_by(
                    col(KppkProduct.product_num).nulls_last(),
                    col(KppkProduct.name),
                    col(KppkOrg.name),
                    col(KppkOrgProduct.id),
                )
            )
        elif dictionary_request.section == "kppkRaws":
            ids = select(KppkRaw.id).order_by(col(KppkRaw.raw_num).nulls_last(), col(KppkRaw.name), col(KppkRaw.id))
        elif dictionary_request.section == "kppkOrgs":
            ids = select(KppkOrg.id).order_by(col(KppkOrg.name), col(KppkOrg.id))
        else:
            ids = (
                select(User.id)
                .join(UserRole, col(UserRole.user_id) == col(User.id))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(col(Project.key) == KPPK_PROJECT_KEY, col(User.is_deleted).is_(False))
                .order_by(func.coalesce(User.name, User.email), col(User.id))
            )

        async def load(row_ids: list[UUID]) -> Sequence[BaseModel]:
            if dictionary_request.section == "kppkPositions":
                return await read_product_position_rows(session, row_ids=row_ids)
            if dictionary_request.section == "kppkRaws":
                return await read_raw_rows(session, row_ids=row_ids)
            if dictionary_request.section == "kppkOrgs":
                return await read_org_rows(session, row_ids=row_ids)
            return await read_kppk_user_rows(session, row_ids=row_ids)

        lists = None
        if any(column.key in REFERENCE_LISTS for column in dictionary_request.columns):
            lists = {"orgs": await read_org_names(session)}
        source = TableSource(
            ids=ids,
            load=load,
            title=safe_sheet_name(SECTION_SHEET_NAMES[dictionary_request.section]),
            columns=dictionary_request.columns,
            header_pinned=dictionary_request.headerPinned,
            lists=lists,
            column_lists=REFERENCE_LISTS,
        )
        source.select_rows(dictionary_request.rowIds)
        return source

    request = KppkRealizationExportRequest.model_validate(spec.request)
    is_cost = spec.source == "kppk_cost"
    if is_cost:
        validate_export_shape(request)
        validate_columns_allowed(request.columns, COST_KEYS[request.slice])
    else:
        validate_realization_export(request)
    if await session.get(KppkOrg, request.orgId) is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    period = await read_current_period(session, month=request.month, year=request.year)
    # Resolve the default month at acceptance, so a delayed job never drifts
    # into a new reporting period while waiting in the queue.
    request.month, request.year = period.periodDate.month, period.periodDate.year
    spec.request = request.model_dump(mode="json")
    model = KppkCost if is_cost else (KppkRealization if request.slice == "fact" else KppkRealizationPlan)
    query = (
        select(model.id)
        .join(KppkProduct, col(KppkProduct.id) == col(model.product_id))
        .where(col(model.org_id) == request.orgId, col(model.period_date) == period.periodDate)
        .order_by(func.coalesce(col(KppkProduct.product_num), MAX_PRODUCT_NUMBER), col(KppkProduct.name))
    )
    if model is not KppkCost:
        query = query.join(KppkMarket, col(KppkMarket.id) == col(model.market_id)).where(
            col(model.rep_date) == period.repDate
        )
        query = query.order_by(text(f"{market_order_sql('kppk_markets.code')} NULLS LAST"), col(KppkMarket.code))
    query = query.order_by(col(model.id))

    async def load(ids: list[UUID]) -> list[BaseModel]:
        if model is KppkCost:
            rows = (
                await session.exec(
                    select(KppkCost, KppkProduct.name, KppkProduct.product_num)
                    .join(KppkProduct, col(KppkProduct.id) == col(KppkCost.product_id))
                    .where(col(KppkCost.id).in_(ids))
                )
            ).all()
            return [_cost_row_read(row, name, number) for row, name, number in rows]
        rows = (
            await session.exec(
                select(model, KppkProduct.name, KppkProduct.product_num, KppkMarket.code)
                .join(KppkProduct, col(KppkProduct.id) == col(model.product_id))
                .join(KppkMarket, col(KppkMarket.id) == col(model.market_id))
                .where(col(model.id).in_(ids))
            )
        ).all()
        if model is KppkRealization:
            return [_fact_realization_row_read(row, name, number, code) for row, name, number, code in rows]
        return [_plan_realization_row_read(row, name, number, code) for row, name, number, code in rows]

    source = TableSource(
        ids=query,
        load=load,
        title="Себестоимость" if is_cost else "Реализация",
        columns=request.columns,
        header_pinned=request.headerPinned,
        merge_rows_by=() if is_cost else REALIZATION_MERGE_BY,
    )
    source.select_rows(request.rowIds)
    return source
