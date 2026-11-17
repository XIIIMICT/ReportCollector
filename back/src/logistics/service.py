"""«Логистика» access resolution: only the project administrator role exists."""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.logistics.constants import LOGISTICS_PROJECT_KEY

from .core import NoLogisticsAccessError


async def resolve_logistics_access(
    session: AsyncSession, identity: CurrentUserIdentity
) -> tuple[User, list[ProjectRole]]:
    """Return (user, active roles of the logistics project); raises without access."""
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoLogisticsAccessError
    roles = list(
        (
            await session.exec(
                select(col(UserRole.role))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(
                    col(UserRole.user_id) == user.id,
                    col(Project.key) == LOGISTICS_PROJECT_KEY,
                    col(UserRole.is_active).is_(True),
                )
            )
        ).all()
    )
    if not roles:
        raise NoLogisticsAccessError
    return user, roles


def is_logistics_admin(roles: list[ProjectRole]) -> bool:
    return ProjectRole.ADMIN in roles
