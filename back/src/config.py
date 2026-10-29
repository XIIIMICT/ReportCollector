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
    database_pool_size: int = Field(default=10, validation_alias="DATABASE_POOL_SIZE", ge=1)
    database_max_overflow: int = Field(default=10, validation_alias="DATABASE_MAX_OVERFLOW", ge=0)
    database_pool_timeout_seconds: float = Field(default=10.0, validation_alias="DATABASE_POOL_TIMEOUT_SECONDS", gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, validation_alias="DATABASE_POOL_RECYCLE_SECONDS", ge=-1)
    database_statement_timeout_ms: int = Field(default=60_000, validation_alias="DATABASE_STATEMENT_TIMEOUT_MS", gt=0)
    database_lock_timeout_ms: int = Field(default=30_000, validation_alias="DATABASE_LOCK_TIMEOUT_MS", gt=0)
    valkey_url: str = Field(default="redis://127.0.0.1:6379/0", validation_alias="VALKEY_URL")
    keycloak_enabled: bool = Field(default=False, validation_alias="KEYCLOAK_ENABLED")
    auth_secret: str = ""
    auth_seed_admin_email: str | None = None
    auth_seed_admin_password: str | None = None
    auth_identity_ttl: int = Field(default=60, validation_alias="AUTH_IDENTITY_TTL")
    renovation_import_max_bytes: int = Field(
        default=256 * 1024 * 1024,
        validation_alias="RENOVATION_IMPORT_MAX_BYTES",
        gt=0,
    )
    renovation_upload_timeout_seconds: float = Field(
        default=300.0, validation_alias="RENOVATION_UPLOAD_TIMEOUT_SECONDS", gt=0
    )
    # Number of trusted reverse proxies in front of the app. 0 (default) means
    # X-Forwarded-For is ignored and the rate limiter keys on the socket peer;
    # N>0 makes it take the client from the Nth-from-right XFF entry.
    trusted_proxy_hops: int = Field(default=0, validation_alias="TRUSTED_PROXY_HOPS")
    # Defaults to local dev origins. In the compose stack the value is derived
    # from AUTH_APP_ORIGIN (the single source of the app host); here it matters
    # only when running the backend on the host outside compose.
    backend_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5000", "http://127.0.0.1:5000"]
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _normalize_cors_origins(cls, value: object) -> object:
        """
        Strip the trailing slash from origins.

        The browser never sends a slash in Origin, while the user may write
        AUTH_APP_ORIGIN (which compose derives the list from) with a slash —
        otherwise the WS/HTTP handshake is wrongly rejected.
        """
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, list):
            return [str(origin).rstrip("/") for origin in value]
        return value

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
        if len(self.auth_secret) < 32:
            msg = "AUTH_SECRET должен содержать не менее 32 символов."
            raise ValueError(msg)
        if bool(self.auth_seed_admin_email) != bool(self.auth_seed_admin_password):
            msg = "AUTH_SEED_ADMIN_EMAIL и AUTH_SEED_ADMIN_PASSWORD должны задаваться вместе."
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
