"""The «Логистика» seed: the project, the hazard classes and Евтушенко's access."""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.logistics.constants import LOGISTICS_PROJECT_KEY
from src.logistics.models import LogisticsHazardClass
from src.scripts.seed_logistics import LOGISTICS_ADMIN_EMAILS, seed_logistics


async def test_seed_logistics_is_idempotent_and_grants_evtushenko_access(
    db_session: AsyncSession,
) -> None:
    first = await seed_logistics(db_session)
    assert first["admins_created"] == 1
    assert first["admins_activated"] == 1

    second = await seed_logistics(db_session)
    assert second == {"classes_created": 0, "admins_created": 0, "admins_activated": 0}

    project = (await db_session.exec(select(Project).where(col(Project.key) == LOGISTICS_PROJECT_KEY))).one()
    classes = (await db_session.exec(select(LogisticsHazardClass.value).order_by(LogisticsHazardClass.value))).all()
    assert classes == list(range(1, 10))

    person = (await db_session.exec(select(User).where(col(User.email) == LOGISTICS_ADMIN_EMAILS[0]))).one()
    link = await db_session.get(UserRole, (person.id, project.id))
    assert link is not None
    assert link.role == ProjectRole.ADMIN
    assert link.is_active is True
    # The seed touches ONLY logistics: no membership in any other project.
    links = (await db_session.exec(select(UserRole).where(col(UserRole.user_id) == person.id))).all()
    assert {item.project_id for item in links} == {project.id}


async def test_seed_logistics_reactivates_a_disabled_admin_link(db_session: AsyncSession) -> None:
    await seed_logistics(db_session)
    project = (await db_session.exec(select(Project).where(col(Project.key) == LOGISTICS_PROJECT_KEY))).one()
    person = (await db_session.exec(select(User).where(col(User.email) == LOGISTICS_ADMIN_EMAILS[0]))).one()
    link = await db_session.get(UserRole, (person.id, project.id))
    assert link is not None
    link.is_active = False
    link.role = ProjectRole.ASSET_WORKER
    await db_session.flush()

    result = await seed_logistics(db_session)
    assert result["admins_activated"] == 1
    refreshed = await db_session.get(UserRole, (person.id, project.id))
    assert refreshed is not None
    assert refreshed.role == ProjectRole.ADMIN
    assert refreshed.is_active is True
