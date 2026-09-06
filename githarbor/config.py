from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INTERVAL = re.compile(r"^(?P<value>[1-9]\d*)\s*(?P<unit>s|m|h|d)?$", re.IGNORECASE)


class ReleaseAssetMode(StrEnum):
    ALL = "all"
    LATEST = "latest"


class ContainerImageMode(StrEnum):
    ALL = "all"
    LATEST = "latest"


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
    sync_concurrency: int = Field(default=3, ge=1, le=32)
    database_path: Path = Path("/data/githarbor.db")
    destination_private: bool = True
    wiki_enabled: bool = True
    releases_enabled: bool = True
    release_assets_enabled: bool = True
    release_asset_mode: ReleaseAssetMode = ReleaseAssetMode.ALL
    packages_enabled: bool = False
    github_container_registry: str = "ghcr.io"
    container_image_mode: ContainerImageMode = ContainerImageMode.ALL
    package_max_bytes: int = Field(default=0, ge=0)
    package_transfer_timeout_seconds: int = Field(default=3600, ge=30)
    git_lfs_enabled: bool = True
    git_pull_refs_enabled: bool = False
    git_timeout_seconds: int = Field(default=3600, ge=30)
    git_cache_path: Path = Path("/data/git-mirrors")
    git_cache_retention_days: int = Field(default=30, ge=0, le=3650)
    external_sources_file: Path | None = None
    api_timeout_seconds: int = Field(default=30, ge=5)
    release_asset_timeout_seconds: int = Field(default=3600, ge=30)
    admin_actions_enabled: bool = False
    admin_actions_token: SecretStr | None = None
    log_level: str = "INFO"

    @field_validator("sync_interval", mode="before")
    @classmethod
    def validate_interval(cls, value: str | int) -> int:
        return parse_interval(value)

    @field_validator("external_sources_file", mode="before")
    @classmethod
    def validate_external_sources_file(cls, value: str | Path | None) -> str | Path | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

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

    @field_validator("release_asset_mode", mode="before")
    @classmethod
    def validate_release_asset_mode(cls, value: str | ReleaseAssetMode) -> str:
        return str(value).strip().lower()

    @field_validator("container_image_mode", mode="before")
    @classmethod
    def validate_container_image_mode(cls, value: str | ContainerImageMode) -> str:
        return str(value).strip().lower()

    @field_validator("github_container_registry")
    @classmethod
    def validate_container_registry(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9.-]+(?::[1-9]\d{0,4})?", value):
            raise ValueError("must be a registry hostname with an optional port")
        return value

    @model_validator(mode="after")
    def validate_package_settings(self) -> Settings:
        if not self.packages_enabled:
            return self
        parsed = urlsplit(str(self.gitea_url))
        if parsed.path.rstrip("/") or parsed.query or parsed.fragment:
            raise ValueError("GITEA_URL must be an instance root when package mirroring is enabled")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "GITEA_URL cannot contain credentials when package mirroring is enabled"
            )
        return self

    @model_validator(mode="after")
    def validate_admin_actions(self) -> Settings:
        if self.admin_actions_enabled and (
            self.admin_actions_token is None
            or not self.admin_actions_token.get_secret_value().strip()
        ):
            raise ValueError("ADMIN_ACTIONS_TOKEN is required when ADMIN_ACTIONS_ENABLED=true")
        return self

    @property
    def admin_actions_available(self) -> bool:
        return self.admin_actions_enabled and self.admin_actions_token is not None

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def github_api_base(self) -> str:
        return str(self.github_api_url).rstrip("/")

    @property
    def gitea_api_base(self) -> str:
        return f"{str(self.gitea_url).rstrip('/')}/api/v1"

    @property
    def gitea_registry(self) -> str:
        parsed = urlsplit(str(self.gitea_url))
        if not parsed.netloc:
            raise ValueError("GITEA_URL does not contain a registry hostname")
        return parsed.netloc

    @property
    def gitea_registry_tls_verify(self) -> bool:
        return urlsplit(str(self.gitea_url)).scheme.casefold() == "https"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
