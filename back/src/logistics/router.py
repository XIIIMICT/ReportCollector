"""«Логистика» API: read-only dictionaries backed by the external `dict` schema."""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.database import get_session

from .dictionaries import read_countries, read_hazard_classes, read_orgs, read_transport_types
from .schemas import (
    LogisticsCountryRowRead,
    LogisticsDictionaryRead,
    LogisticsHazardClassRowRead,
    LogisticsOrgRowRead,
    LogisticsTransportRowRead,
)
from .service import NoLogisticsAccessError, is_logistics_admin, resolve_logistics_access

if TYPE_CHECKING:
    from src.auth.models import ProjectRole, User

router = APIRouter(prefix="/logistics", tags=["logistics"])

SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
IdentityDependency = Annotated[CurrentUserIdentity, Depends(read_signed_identity)]


class LogisticsAccess:
    """Resolved access of the current user to the «Логистика» project."""

    def __init__(self, *, user: User, roles: list[ProjectRole]) -> None:
        self.user = user
        self.roles = roles
        self.is_admin = is_logistics_admin(roles)


async def require_logistics_admin(
    session: SessionDependency,
    identity: IdentityDependency,
) -> LogisticsAccess:
    """
    Resolve the user's «Логистика» access; 403 without the project admin role.

    Only the administrator role exists in this project phase, so every surface
    (including the read-only dictionaries) is admin-only.
    """
    try:
        user, roles = await resolve_logistics_access(session, identity)
    except NoLogisticsAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Логистика».",
        ) from error
    access = LogisticsAccess(user=user, roles=roles)
    if not access.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «Логистика».",
        )
    return access


AdminDependency = Annotated[LogisticsAccess, Depends(require_logistics_admin)]


@router.get("/dictionaries/countries", response_model=LogisticsDictionaryRead[LogisticsCountryRowRead])
async def get_countries(
    session: SessionDependency,
    _access: AdminDependency,
) -> LogisticsDictionaryRead[LogisticsCountryRowRead]:
    """Country short names from the external dictionary (demo values if absent)."""
    return await read_countries(session)


@router.get(
    "/dictionaries/transport-types",
    response_model=LogisticsDictionaryRead[LogisticsTransportRowRead],
)
async def get_transport_types(
    session: SessionDependency,
    _access: AdminDependency,
) -> LogisticsDictionaryRead[LogisticsTransportRowRead]:
    """Transport types with their vehicle types (demo values if the tables are absent)."""
    return await read_transport_types(session)


@router.get("/dictionaries/orgs", response_model=LogisticsDictionaryRead[LogisticsOrgRowRead])
async def get_orgs(
    session: SessionDependency,
    _access: AdminDependency,
) -> LogisticsDictionaryRead[LogisticsOrgRowRead]:
    """Organization prefixes from the external dictionary (demo values if absent)."""
    return await read_orgs(session)


@router.get("/dictionaries/hazard-classes", response_model=list[LogisticsHazardClassRowRead])
async def get_hazard_classes(
    session: SessionDependency,
    _access: AdminDependency,
) -> list[LogisticsHazardClassRowRead]:
    """Return the «Класс опасности» dictionary (1..9); no tab yet, used by the main table."""
    return await read_hazard_classes(session)
