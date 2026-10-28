from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INVENTORY_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).drivername != "postgresql+psycopg":
            raise ValueError("Use a postgresql+psycopg database URL; SQLite is not supported")
        return value


class Settings(DatabaseSettings):
    api_key: SecretStr
    lock_timeout_ms: int = Field(default=3000, ge=50, le=10000)
    statement_timeout_ms: int = Field(default=10000, ge=100, le=30000)

    @field_validator("api_key")
    @classmethod
    def require_key_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 24:
            raise ValueError("API key must contain at least 24 characters")
        return value

    @model_validator(mode="after")
    def bound_timeouts(self) -> Self:
        if self.statement_timeout_ms <= self.lock_timeout_ms:
            raise ValueError("Statement timeout must exceed lock timeout")
        return self
