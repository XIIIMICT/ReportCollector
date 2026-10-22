"""User-backed audit storage with a read-only display-name SQL projection."""

from sqlalchemy import column, func, inspect, select, table
from sqlalchemy.orm import column_property, object_session
from sqlmodel import SQLModel


class UpdaterDisplay(SQLModel):
    """A read-only name for APIs; only updater_user_id is stored."""

    @property
    def updated_by(self) -> str:
        user_id = getattr(self, "updater_user_id", None)
        if user_id is None:
            return ""
        # Mutations already hold the acting User in the session. Prefer it so
        # immediate responses do not show a projection loaded before the edit.
        session = object_session(self)
        if session is not None:
            from src.auth.models import User  # noqa: PLC0415

            user = session.identity_map.get((User, (user_id,), None))
            if user is not None:
                return (
                    user.__dict__.get("name") or user.__dict__.get("email") or self.__dict__.get("_updater_name") or ""
                )
        return self.__dict__.get("_updater_name") or ""


def configure_updater(model: type[SQLModel]) -> None:
    """Load the API display name in the row query, never with per-row IO."""
    mapper = inspect(model)
    users = table("app_users", column("id"), column("name"), column("email")).alias("audit_user")
    mapper.add_property(
        "_updater_name",
        column_property(
            select(func.coalesce(func.nullif(users.c.name, ""), users.c.email, ""))
            .where(users.c.id == mapper.local_table.c.updater_user_id)
            .correlate_except(users)
            .scalar_subquery(),
            expire_on_flush=False,
        ),
    )
