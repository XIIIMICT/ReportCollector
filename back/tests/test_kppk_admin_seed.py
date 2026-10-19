"""The administrator seed is idempotent and scoped to KPPK."""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.scripts.seed_kppk import KPPK_ADMIN_EMAILS, seed_administrators


async def test_kppk_seed_ensures_admins_without_changing_other_projects(db_session: AsyncSession) -> None:
    projects = {project.key: project.id for project in (await db_session.exec(select(Project))).all()}
    person = User(email="lavrova.mg@ruschem.ru", name="Existing identity")
    db_session.add(person)
    await db_session.flush()
    raw_link = UserRole(
        user_id=person.id, project_id=projects["raw_materials"], role=ProjectRole.ASSET_WORKER, is_active=True
    )
    db_session.add(raw_link)
    await db_session.flush()
    assert await seed_administrators(db_session) == 2
    assert await seed_administrators(db_session) == 2
    for email in KPPK_ADMIN_EMAILS:
        user = (await db_session.exec(select(User).where(col(User.email) == email))).one()
        link = await db_session.get(UserRole, (user.id, projects["kppk"]))
        assert link is not None
        assert link.is_active
        assert link.role == ProjectRole.ADMIN
    assert person.name == "Existing identity"
    await db_session.refresh(raw_link)
    assert raw_link.role == ProjectRole.ASSET_WORKER
    assert raw_link.is_active


async def test_kppk_seed_creates_lavrova_when_missing(db_session: AsyncSession) -> None:
    await seed_administrators(db_session)
    user = (await db_session.exec(select(User).where(col(User.email) == "lavrova.mg@ruschem.ru"))).one()
    assert user.email == "lavrova.mg@ruschem.ru"
