"""
Shared «Логистика» errors and identity lookup.

This module must not import any project service implementation.
"""

from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project
from src.logistics.constants import LOGISTICS_PROJECT_KEY


class NoLogisticsAccessError(Exception):
    """The user has no access to the project."""


class LogisticsNotFoundError(ValueError):
    """The requested record does not exist."""


class LogisticsValidationError(ValueError):
    """The request violates a domain validation rule."""


async def logistics_project_id(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == LOGISTICS_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        message = "Проект «Логистика» не найден."
        raise LogisticsNotFoundError(message)
    return project_id
