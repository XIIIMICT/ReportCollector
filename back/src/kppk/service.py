"""
KPPK access resolution.

Source dictionaries and their journal replay live in dictionaries.py;
realization slices and project users have their own service modules.
"""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.kppk.constants import KPPK_PROJECT_KEY

from .core import NoKppkAccessError

# ─── Access ────────────────────────────────────────────────────────────────


async def resolve_kppk_access(session: AsyncSession, identity: CurrentUserIdentity) -> tuple[User, list[ProjectRole]]:
    """Return (user, active roles of the kppk project); raises without access."""
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoKppkAccessError
    roles = list(
        (
            await session.exec(
                select(col(UserRole.role))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(
                    col(UserRole.user_id) == user.id,
                    col(Project.key) == KPPK_PROJECT_KEY,
                    col(UserRole.is_active).is_(True),
                )
            )
        ).all()
    )
    if not roles:
        raise NoKppkAccessError
    return user, roles


def is_kppk_admin(roles: list[ProjectRole]) -> bool:
    return ProjectRole.ADMIN in roles
