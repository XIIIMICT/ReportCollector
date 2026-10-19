"""Plan identity lookups shared by renovation read models and mutations."""

from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.plan import RenovationPlan, RPlanState


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
