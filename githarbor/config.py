from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INTERVAL = re.compile(r"^(?P<value>[1-9]\d*)\s*(?P<unit>s|m|h|d)?$", re.IGNORECASE)


def parse_interval(value: str | int) -> int:
    if isinstance(value, int):
        if value < 1:
            raise ValueError("SYNC_INTERVAL must be positive")
        return value
    match = _INTERVAL.fullmatch(value.strip())
    if not match:
        raise ValueError("SYNC_INTERVAL must look like 30m, 6h, 1d, or a number of seconds")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(match.group("value")) * multipliers[match.group("unit") or "s"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    github_token: SecretStr
    github_username: str = Field(min_length=1)
    github_api_url: HttpUrl = HttpUrl("https://api.github.com")
    gitea_url: HttpUrl
    gitea_token: SecretStr
    gitea_owned_namespace: str = Field(min_length=1)
    gitea_starred_namespace: str = Field(min_length=1)
    sync_interval: int = 21600
    sync_on_startup: bool = True
    database_path: Path = Path("/data/githarbor.db")
    destination_private: bool = True
    git_lfs_enabled: bool = True
    git_timeout_seconds: int = Field(default=3600, ge=30)
    api_timeout_seconds: int = Field(default=30, ge=5)
    release_asset_timeout_seconds: int = Field(default=3600, ge=30)
    log_level: str = "INFO"

    @field_validator("sync_interval", mode="before")
    @classmethod
    def validate_interval(cls, value: str | int) -> int:
        return parse_interval(value)

    @field_validator("github_username", "gitea_owned_namespace", "gitea_starred_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("must contain only letters, numbers, dots, underscores, and hyphens")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def github_api_base(self) -> str:
        return str(self.github_api_url).rstrip("/")

    @property
    def gitea_api_base(self) -> str:
        return f"{str(self.gitea_url).rstrip('/')}/api/v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
