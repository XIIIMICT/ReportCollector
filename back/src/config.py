import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ReportCollector API"
    log_file: str = "logs/reportcollector.log"
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None
    postgres_host: str | None = None
    postgres_port: int | None = None
    postgres_schema: str | None = None
    auth_secret: str = ""
    auth_seed_admin_email: str | None = None
    auth_seed_admin_name: str | None = None
    backend_cors_origins: Annotated[list[str], NoDecode] | None = None

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        if self.database_url is None:
            missing = [
                name
                for name, value in {
                    "POSTGRES_USER": self.postgres_user,
                    "POSTGRES_PASSWORD": self.postgres_password,
                    "POSTGRES_DB": self.postgres_db,
                    "POSTGRES_HOST": self.postgres_host,
                    "POSTGRES_PORT": self.postgres_port,
                    "POSTGRES_SCHEMA": self.postgres_schema,
                }.items()
                if value is None or value == ""
            ]
            if missing:
                msg = f"Не заданы обязательные переменные: {', '.join(missing)}."
                raise ValueError(msg)
        if self.backend_cors_origins is None:
            msg = "Не задана обязательная переменная BACKEND_CORS_ORIGINS."
            raise ValueError(msg)
        if len(self.auth_secret) < 32:
            msg = "AUTH_SECRET должен содержать не менее 32 символов."
            raise ValueError(msg)
        if bool(self.auth_seed_admin_email) != bool(self.auth_seed_admin_name):
            msg = "AUTH_SEED_ADMIN_EMAIL и AUTH_SEED_ADMIN_NAME должны задаваться вместе."
            raise ValueError(msg)
        return self

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_backend_cors_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        stripped_value = value.strip()
        if stripped_value.startswith("["):
            return json.loads(stripped_value)

        return [origin.strip() for origin in stripped_value.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url is not None:
            return self.database_url

        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
