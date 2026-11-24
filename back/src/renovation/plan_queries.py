"""Plan identity lookups and working-plan aggregates shared by renovation read models."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState


@dataclass(frozen=True, slots=True)
class WorkingPlanAggregate:
    """The computed columns of one dictionary value: repair works and their cost."""

    repair_count: int = 0
    total_cost: float = 0.0


async def _renovation_project_id(session: AsyncSession) -> UUID | None:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one_or_none()
    return project_id


async def _plan_by_state(session: AsyncSession, state: RPlanState) -> RenovationPlan | None:
    project_id = await _renovation_project_id(session)
    if project_id is None:
        return None
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.project_id) == project_id, col(RenovationPlan.state) == state)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _plan_id_by_state(session: AsyncSession, state: RPlanState) -> UUID | None:
    plan = await _plan_by_state(session, state)
    return plan.id if plan is not None else None


async def read_working_plan_aggregates(
    session: AsyncSession,
    *,
    group_by: InstrumentedAttribute[object],
    ids: Sequence[object],
) -> dict[object, WorkingPlanAggregate]:
    """
    «Число ремонтных работ» and «Стоимость итого» of the working plan, by value.

    Every renovation dictionary groups the working plan's rows by the column
    that stores its value — `asset_id` for «Предприятия», the reference column
    for a catalog option. A plan row carries exactly one value per reference
    catalog, so a repair work is counted once. The cost is the sum of `total_cost_rub_ex_vat`.

    Values without rows get a zero aggregate, so every requested id is present
    in the result. A project without a working version yields all zeros.
    """
    keys = list(ids)
    if not keys:
        return {}
    aggregates = dict.fromkeys(keys, WorkingPlanAggregate())
    working_plan_id = await _plan_id_by_state(session, RPlanState.WORKING)
    if working_plan_id is None:
        return aggregates
    rows = (
        await session.exec(
            select(
                group_by,
                func.coalesce(func.sum(col(RenovationPlanRow.total_cost_rub_ex_vat)), 0),
                func.count(),
            )
            .where(col(RenovationPlanRow.plan_id) == working_plan_id)
            .where(group_by.in_(keys))
            .group_by(group_by)
        )
    ).all()
    for group, total, count in rows:
        if group is not None:
            aggregates[group] = WorkingPlanAggregate(repair_count=int(count), total_cost=float(total or 0))
    return aggregates
