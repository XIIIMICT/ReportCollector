"""
Seed the «Логистика» data maintained on our side.

The external `dict` schema is NEVER touched: its tables belong to the corporate
logistics database and this application only reads them. This script ensures:

- the `logistics` project row (migration 0032 normally creates it);
- the application-owned «Класс опасности» dictionary (values 1..9, table
  `l_hazard_classes`);
- the project administrator link for Евтушенко (active, admin role), without
  touching his identity or his memberships in other projects.

Idempotent: running it again creates nothing and only re-activates the admin
link if it was disabled.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.database import async_session_factory
from src.logistics.constants import LOGISTICS_PROJECT_KEY, LOGISTICS_PROJECT_NAME
from src.logistics.models import LogisticsHazardClass

# The logistics project has only the administrator role in this phase.
LOGISTICS_ADMIN_EMAILS: tuple[str, ...] = ("evtushenko.sn@ruschem.ru",)
LOGISTICS_ADMIN_NAME = "Евтушенко Сергей Николаевич"


async def seed_logistics(session: AsyncSession) -> dict[str, int]:
    """Ensure the project, the hazard classes and the logistics admins."""
    now = datetime.now(UTC)

    project = (await session.exec(select(Project).where(col(Project.key) == LOGISTICS_PROJECT_KEY))).one_or_none()
    if project is None:
        project = Project(key=LOGISTICS_PROJECT_KEY, name=LOGISTICS_PROJECT_NAME, created_at=now)
        session.add(project)
        await session.flush()

    existing_values = set((await session.exec(select(LogisticsHazardClass.value))).all())
    classes_created = 0
    for value in range(1, 10):
        if value in existing_values:
            continue
        session.add(LogisticsHazardClass(value=value, name=f"{value} класс"))
        classes_created += 1

    admins_created = 0
    admins_activated = 0
    for email in LOGISTICS_ADMIN_EMAILS:
        person = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
        if person is None:
            person = User(email=email, name=LOGISTICS_ADMIN_NAME, created_at=now, updated_at=now)
            session.add(person)
            await session.flush()
            admins_created += 1
        link = await session.get(UserRole, (person.id, project.id))
        if link is None:
            session.add(
                UserRole(
                    user_id=person.id,
                    project_id=project.id,
                    role=ProjectRole.ADMIN,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            admins_activated += 1
        else:
            changed = link.role != ProjectRole.ADMIN or not link.is_active
            link.role = ProjectRole.ADMIN
            link.is_active = True
            session.add(link)
            if changed:
                admins_activated += 1
    await session.flush()
    return {
        "classes_created": classes_created,
        "admins_created": admins_created,
        "admins_activated": admins_activated,
    }


async def run() -> None:
    async with async_session_factory() as session:
        result = await seed_logistics(session)
        await session.commit()
    print(
        "Сидирование логистики завершено: "
        f"классов опасности добавлено={result['classes_created']}, "
        f"администраторов создано={result['admins_created']}, "
        f"связей активировано={result['admins_activated']}."
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
