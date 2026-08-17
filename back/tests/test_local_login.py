"""Password auth: email+password login, first login, password reset."""

from sqlmodel import col, select

from src.auth.constants import LOCAL_USER_KEY
from src.auth.models import User
from src.auth.passwords import hash_password, validate_new_password, verify_password
from src.auth.schemas import LocalLoginResponse

ADMIN_EMAIL = "admin@example.ru"
PASSWORD = "Secret123"  # noqa: S105

LOGIN_URL = "/api/auth/login"


async def test_hash_roundtrip() -> None:
    encoded = hash_password(PASSWORD)

    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong-password", encoded)


async def test_hash_validation() -> None:
    import pytest

    with pytest.raises(ValueError, match="не менее 8"):
        validate_new_password("short")
    with pytest.raises(ValueError, match="слишком длинный"):
        validate_new_password("с" * 1030)


async def test_local_login_sets_first_password(client, db_session) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    assert user.password_hash is None

    response = await client.post(LOGIN_URL, json={"email": ADMIN_EMAIL, "password": PASSWORD})

    assert response.status_code == 200
    body: LocalLoginResponse = LocalLoginResponse.model_validate(response.json())
    assert body.email == ADMIN_EMAIL
    assert body.sub == f"{LOCAL_USER_KEY}:{ADMIN_EMAIL}"

    await db_session.refresh(user)
    assert user.password_hash is not None
    assert verify_password(PASSWORD, user.password_hash)


async def test_local_login_wrong_password(client, db_session) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    user.password_hash = hash_password(PASSWORD)
    await db_session.commit()

    response = await client.post(LOGIN_URL, json={"email": ADMIN_EMAIL, "password": "WrongPass1"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный email или пароль."


async def test_local_login_unknown_email(client) -> None:
    response = await client.post(LOGIN_URL, json={"email": "ghost@example.ru", "password": PASSWORD})

    assert response.status_code == 401


async def test_local_login_email_case_insensitive(client, db_session) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    user.password_hash = hash_password(PASSWORD)
    await db_session.commit()

    response = await client.post(LOGIN_URL, json={"email": ADMIN_EMAIL.upper(), "password": PASSWORD})

    assert response.status_code == 200


async def test_reset_password_sets_new_hash(client, db_session) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    user.password_hash = hash_password("OldPassword1")
    await db_session.commit()

    await client.get("/api/me", headers=_admin_headers())
    response = await client.post(
        f"/api/auth/users/{user.id}/password",
        json={"new_password": "NewPassword1"},
        headers=_admin_headers(),
    )

    assert response.status_code == 204
    await db_session.refresh(user)
    assert verify_password("NewPassword1", user.password_hash)
    assert not verify_password("OldPassword1", user.password_hash)


async def test_reset_password_clears_hash(client, db_session) -> None:
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    user.password_hash = hash_password(PASSWORD)
    await db_session.commit()

    response = await client.post(
        f"/api/auth/users/{user.id}/password",
        json={"new_password": ""},
        headers=_admin_headers(),
    )

    assert response.status_code == 204
    await db_session.refresh(user)
    assert user.password_hash is None


async def test_update_profile_theme(client, db_session) -> None:
    response = await client.patch("/api/me", json={"theme": "dark"}, headers=_admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["theme"] == "dark"
    assert body["email"] == ADMIN_EMAIL
    assert body["name"] == "Admin User"

    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    assert user.theme == "dark"


async def test_update_profile_password(client, db_session) -> None:
    response = await client.patch("/api/me", json={"password": "NewProfile1"}, headers=_admin_headers())

    assert response.status_code == 200
    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    assert verify_password("NewProfile1", user.password_hash)


async def test_update_profile_bad_password(client, db_session) -> None:
    response = await client.patch("/api/me", json={"password": "short"}, headers=_admin_headers())

    assert response.status_code == 400


async def test_update_profile_bad_theme(client, db_session) -> None:
    response = await client.patch("/api/me", json={"theme": "neon"}, headers=_admin_headers())

    assert response.status_code == 422


async def test_reset_password_requires_admin(client, db_session) -> None:
    from conftest import signed_identity_headers

    user = (await db_session.exec(select(User).where(col(User.email) == ADMIN_EMAIL))).one()
    headers = signed_identity_headers(email="worker@example.ru", subject="worker-sub")
    await client.get("/api/me", headers=headers)

    response = await client.post(
        f"/api/auth/users/{user.id}/password", json={"new_password": "Whatever1"}, headers=headers
    )

    assert response.status_code == 403


def _admin_headers() -> dict[str, str]:
    from conftest import signed_identity_headers

    return signed_identity_headers(email=ADMIN_EMAIL, preferred_username="admin", subject="admin-sub")
