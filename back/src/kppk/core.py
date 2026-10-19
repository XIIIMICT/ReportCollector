"""
Shared KPPK errors, identity lookup and audit primitives.

This module must not import any project service implementation.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.kppk.constants import KPPK_PROJECT_KEY


class NoKppkAccessError(Exception):
    """The user has no access to the project."""


class KppkNotFoundError(ValueError):
    """The requested row does not exist."""


class KppkValidationError(ValueError):
    """The edit violates a domain validation rule."""


def _audit_name(user: User) -> str:
    return user.name or user.email or ""


def _now() -> datetime:
    return datetime.now(UTC)


async def kppk_project_id(session: AsyncSession) -> UUID:
    project_id = (await session.exec(select(col(Project.id)).where(col(Project.key) == KPPK_PROJECT_KEY))).one_or_none()
    if project_id is None:
        message = "Проект «КППК» не найден."
        raise KppkNotFoundError(message)
    return project_id
