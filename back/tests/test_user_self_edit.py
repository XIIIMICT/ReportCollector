"""User dictionaries cannot change the signed-in user's own row."""

import pytest
from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User

HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


@pytest.mark.parametrize("project", ["raw-materials", "renovation", "kppk"])
async def test_single_user_patch_refuses_self(client: AsyncClient, db_session: AsyncSession, project: str) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    prefix = f"/api/{project}/dictionary" if project == "renovation" else f"/api/{project}"
    response = await client.patch(
        f"{prefix}/users/{user.id}?session_id=self-edit",
        headers=HEADERS,
        json={"isActive": False},
    )
    assert response.status_code == 409, response.text
    assert "собственную запись" in response.json()["detail"]
    await db_session.refresh(user)
    assert user.email == "admin@example.ru"


@pytest.mark.parametrize("project", ["raw-materials", "renovation"])
async def test_user_batch_patch_refuses_self(client: AsyncClient, db_session: AsyncSession, project: str) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    response = await client.patch(
        f"/api/{project}/dictionary?session_id=self-edit",
        headers=HEADERS,
        json={"section": "users", "updates": [{"rowId": str(user.id), "field": "isActive", "value": False}]},
    )
    assert response.status_code == 409, response.text
