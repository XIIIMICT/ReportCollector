"""
KPPK access resolution and backend-only period dictionary operations.

Source dictionaries and their journal replay live in dictionaries.py;
realization slices and project users have their own service modules.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.collab.locking import project_write
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.models import KppkPeriod

from .core import KppkNotFoundError, KppkValidationError, NoKppkAccessError, _audit_name, _now
from .schemas import KppkPeriodRowRead

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


# ─── «Периоды» ────────────────────────────────────────────────────────────


async def read_period_rows(session: AsyncSession) -> list[KppkPeriodRowRead]:
    """Rows of the «Периоды» dictionary ordered chronologically."""
    periods = (await session.exec(select(KppkPeriod).order_by(col(KppkPeriod.first_date)))).all()
    return [
        KppkPeriodRowRead(
            id=period.id,
            monthName=period.month_name,
            yearNum=period.year_num,
            firstDate=period.first_date,
            lastDate=period.last_date,
            createdAt=period.created_at,
            updatedAt=period.updated_at,
            updatedBy=period.updated_by,
        )
        for period in periods
    ]


def _check_period_dates(first_date: date, last_date: date) -> None:
    if last_date < first_date:
        message = "Дата окончания периода не может быть раньше начала."
        raise KppkValidationError(message)


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_period(
    session: AsyncSession,
    *,
    month_name: str,
    year_num: int,
    first_date: date,
    last_date: date,
    user: User,
) -> KppkPeriodRowRead:
    """Create a report month."""
    month_value = " ".join(str(month_name or "").split())
    if not month_value:
        message = "Укажите месяц."
        raise KppkValidationError(message)
    _check_period_dates(first_date, last_date)
    duplicate = (
        await session.exec(
            select(KppkPeriod).where(
                func.lower(col(KppkPeriod.month_name)) == month_value.lower(),
                col(KppkPeriod.year_num) == year_num,
            )
        )
    ).first()
    if duplicate is not None:
        message = f"Период «{month_value} {year_num}» уже существует."
        raise KppkValidationError(message)
    period = KppkPeriod(
        month_name=month_value,
        year_num=year_num,
        first_date=first_date,
        last_date=last_date,
        created_at=_now(),
        updated_at=_now(),
        updated_by=_audit_name(user),
    )
    session.add(period)
    await session.flush()
    return KppkPeriodRowRead(
        id=period.id,
        monthName=period.month_name,
        yearNum=period.year_num,
        firstDate=period.first_date,
        lastDate=period.last_date,
        createdAt=period.created_at,
        updatedAt=period.updated_at,
        updatedBy=period.updated_by,
    )


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_period_cell(
    session: AsyncSession,
    *,
    period_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """Edit one cell of a period row (monthName / yearNum / firstDate / lastDate)."""
    period = await session.get(KppkPeriod, period_id)
    if period is None:
        message = "Период не найден."
        raise KppkNotFoundError(message)
    if field == "monthName":
        month_value = " ".join(str(value or "").split())
        if not month_value:
            message = "Укажите месяц."
            raise KppkValidationError(message)
        duplicate = (
            await session.exec(
                select(KppkPeriod).where(
                    func.lower(col(KppkPeriod.month_name)) == month_value.lower(),
                    col(KppkPeriod.year_num) == period.year_num,
                    col(KppkPeriod.id) != period_id,
                )
            )
        ).first()
        if duplicate is not None:
            message = f"Период «{month_value} {period.year_num}» уже существует."
            raise KppkValidationError(message)
        period.month_name = month_value
    elif field == "yearNum":
        try:
            year_value = int(str(value).strip())
        except (TypeError, ValueError) as error:
            message = "Год — целое число."
            raise KppkValidationError(message) from error
        duplicate = (
            await session.exec(
                select(KppkPeriod).where(
                    func.lower(col(KppkPeriod.month_name)) == period.month_name.lower(),
                    col(KppkPeriod.year_num) == year_value,
                    col(KppkPeriod.id) != period_id,
                )
            )
        ).first()
        if duplicate is not None:
            message = f"Период «{period.month_name} {year_value}» уже существует."
            raise KppkValidationError(message)
        period.year_num = year_value
    elif field in {"firstDate", "lastDate"}:
        raw_date = str(value or "").strip()
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError as error:
            message = f"Некорректная дата «{raw_date}»."
            raise KppkValidationError(message) from error
        if field == "firstDate":
            _check_period_dates(parsed, period.last_date)
            period.first_date = parsed
        else:
            _check_period_dates(period.first_date, parsed)
            period.last_date = parsed
    else:
        message = f"Поле «{field}» нельзя изменить."
        raise KppkValidationError(message)
    period.updated_at = _now()
    period.updated_by = _audit_name(user)
    session.add(period)
    await session.flush()


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_period(session: AsyncSession, *, period_id: UUID) -> None:
    """Delete a period that is not referenced by the fact/plan copies."""
    period = await session.get(KppkPeriod, period_id)
    if period is None:
        message = "Период не найден."
        raise KppkNotFoundError(message)
    if period.period_id is not None:
        message = "Этот период из исходной выгрузки — на него ссылается копия данных факта/плана."
        raise KppkValidationError(message)
    await session.delete(period)
    await session.flush()
