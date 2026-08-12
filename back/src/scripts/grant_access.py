"""
Выдаёт пользователю доступ к проекту raw_materials.

Ручная активация связи пользователя с проектом и назначение ролей:
  uv run --directory back -m src.scripts.grant_access <email> admin
  uv run --directory back -m src.scripts.grant_access <email> asset_worker [asset_name ...]

Включает is_active на связи пользователя с проектом (доступ к проекту) и
назначает роль — то же самое, что администратор делает через справочник
«Пользователи». Для роли asset_worker можно сразу привязать активы
(названия из rm_asset, например СНХЗ, БСЗ, ВОС).
"""

import asyncio
import sys

from sqlmodel import col, select

from src.auth import constants as auth_constants
from src.auth.models import ProjectRole, User
from src.auth.service import activate_project_user, grant_project_assets, grant_project_role
from src.database import async_session_factory


async def run(email: str, role: ProjectRole, asset_names: list[str] | None = None) -> None:
    async with async_session_factory() as session:
        user = (await session.exec(select(User).where(col(User.email) == email))).one_or_none()
        if user is None:
            print(f"Пользователь с email {email!r} не найден. Сначала выполните вход в приложение через Keycloak.")
            sys.exit(1)

        await grant_project_role(session, user.id, auth_constants.RAW_MATERIALS_PROJECT_KEY, role)
        await activate_project_user(session, user.id, auth_constants.RAW_MATERIALS_PROJECT_KEY)
        if asset_names:
            await grant_project_assets(session, user.id, asset_names)
        print(
            f"Пользователь {email!r} активирован (is_active=true) в проекте "
            f"{auth_constants.RAW_MATERIALS_PROJECT_KEY} с ролью {role.value!r}"
            + (f", активы: {', '.join(asset_names)}." if asset_names else ".")
        )


def main() -> None:
    usage = (
        "Использование: uv run --directory back -m src.scripts.grant_access "
        f"<email> [{'|'.join(role.value for role in ProjectRole)}] [asset_name ...]"
    )
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(2)

    email = sys.argv[1]
    if len(sys.argv) < 3:
        print(usage)
        sys.exit(2)

    role_value = sys.argv[2]
    try:
        role = ProjectRole(role_value)
    except ValueError:
        print(f"Неизвестная роль {role_value!r}. Доступны: {', '.join(role.value for role in ProjectRole)}")
        sys.exit(2)

    asset_names = sys.argv[3:] or None
    asyncio.run(run(email, role, asset_names))


if __name__ == "__main__":
    main()
