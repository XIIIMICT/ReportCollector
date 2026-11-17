"""«Логистика» access resolution: administrator, reviewer and asset worker."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.logistics.constants import LOGISTICS_PROJECT_KEY
from src.logistics.models import LogisticsUserAsset

from .core import NoLogisticsAccessError


@dataclass
class LogisticsMembership:
    """The current user's membership: a role and (for workers) asset grants."""

    user: User
    role: ProjectRole
    asset_ids: set[UUID] = field(default_factory=set)

    @property
    def is_admin(self) -> bool:
        return self.role == ProjectRole.ADMIN

    @property
    def is_reviewer(self) -> bool:
        return self.role == ProjectRole.REVIEWER

    @property
    def is_asset_worker(self) -> bool:
        return self.role == ProjectRole.ASSET_WORKER


async def resolve_logistics_access(session: AsyncSession, identity: CurrentUserIdentity) -> LogisticsMembership:
    """
    Return the user's «Логистика» membership; raises without access.

    A blocked role has no access, and an asset worker without a single grant is
    EQUIVALENT to blocked (the admin may still save the role before picking the
    asset — the access rule is checked here, not stored).
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoLogisticsAccessError
    role = (
        await session.exec(
            select(col(UserRole.role))
            .join(Project, col(Project.id) == col(UserRole.project_id))
            .where(
                col(UserRole.user_id) == user.id,
                col(Project.key) == LOGISTICS_PROJECT_KEY,
                col(UserRole.is_active).is_(True),
            )
        )
    ).first()
    if role is None:
        raise NoLogisticsAccessError
    if role == ProjectRole.BLOCKED:
        raise NoLogisticsAccessError
    asset_ids: set[UUID] = set()
    if role == ProjectRole.ASSET_WORKER:
        asset_ids = set(
            (
                await session.exec(
                    select(col(LogisticsUserAsset.org_id)).where(col(LogisticsUserAsset.user_id) == user.id)
                )
            ).all()
        )
        if not asset_ids:
            raise NoLogisticsAccessError
    return LogisticsMembership(user=user, role=role, asset_ids=asset_ids)


def is_logistics_admin(role: ProjectRole) -> bool:
    return role == ProjectRole.ADMIN
