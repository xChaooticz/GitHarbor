from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from githarbor.services.naming import safe_component

_SOURCE_ID = re.compile(r"[A-Za-z0-9_.-]+")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ExternalSourcesError(ValueError):
    pass


class ExternalProvider(StrEnum):
    FORGEJO = "forgejo"
    GITLAB = "gitlab"


def _validated_git_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an HTTP(S) Git URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("must not contain a query string or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class ExternalSourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, min_length=1, max_length=128)
    provider: ExternalProvider
    clone_url: str
    destination_namespace: str = Field(min_length=1, max_length=255)
    destination_name: str | None = Field(default=None, max_length=100)
    web_url: str | None = None
    wiki_url: str | None = None
    api_url: str | None = None
    releases: bool = True
    release_assets: bool = True
    token_env: str | None = None
    git_username: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2048)
    default_branch: str | None = Field(default=None, min_length=1, max_length=255)
    private: bool = False
    archived: bool = False
    fork: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SOURCE_ID.fullmatch(value):
            raise ValueError("must contain only letters, numbers, dots, underscores, and hyphens")
        return value

    @field_validator("destination_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if not _SOURCE_ID.fullmatch(value):
            raise ValueError("must contain only letters, numbers, dots, underscores, and hyphens")
        return value

    @field_validator("destination_name")
    @classmethod
    def validate_destination_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if safe_component(value) != value:
            raise ValueError("must be a valid Gitea repository name")
        return value

    @field_validator("clone_url", "web_url", "wiki_url", "api_url")
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        return _validated_git_url(value) if value is not None else None

    @field_validator("token_env")
    @classmethod
    def validate_token_environment(cls, value: str | None) -> str | None:
        if value is not None and not _ENVIRONMENT_NAME.fullmatch(value):
            raise ValueError("must be a valid environment variable name")
        return value

    @model_validator(mode="after")
    def validate_transport_security(self) -> ExternalSourceEntry:
        if self.token_env and urlsplit(self.clone_url).scheme.casefold() != "https":
            raise ValueError("authenticated external clone_url values must use HTTPS")
        if (
            self.token_env
            and self.wiki_url
            and urlsplit(self.wiki_url).scheme.casefold() != "https"
        ):
            raise ValueError("authenticated external wiki_url values must use HTTPS")
        if self.token_env and self.api_url and urlsplit(self.api_url).scheme.casefold() != "https":
            raise ValueError("authenticated external api_url values must use HTTPS")
        clone = urlsplit(self.clone_url)
        clone_origin = (clone.scheme.casefold(), clone.hostname, clone.port)
        if self.token_env and self.wiki_url:
            wiki = urlsplit(self.wiki_url)
            if clone_origin != (wiki.scheme.casefold(), wiki.hostname, wiki.port):
                raise ValueError("authenticated wiki_url must use the same origin as clone_url")
        if self.api_url:
            api = urlsplit(self.api_url)
            if clone_origin != (
                api.scheme.casefold(),
                api.hostname,
                api.port,
            ):
                raise ValueError("api_url must use the same origin as clone_url")
        return self


class ExternalSourcesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    repositories: list[ExternalSourceEntry] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only external sources file version 1 is supported")
        return value

    @model_validator(mode="after")
    def validate_unique_entries(self) -> ExternalSourcesDocument:
        identities: set[tuple[ExternalProvider, str]] = set()
        source_urls: set[tuple[ExternalProvider, str]] = set()
        destinations: set[tuple[str, str]] = set()
        for entry in self.repositories:
            if entry.id is not None:
                identity = (entry.provider, entry.id)
                if identity in identities:
                    raise ValueError(
                        f"duplicate external source identity: {entry.provider}/{entry.id}"
                    )
                identities.add(identity)
            source_url = (entry.provider, entry.clone_url)
            if source_url in source_urls:
                raise ValueError(f"duplicate external clone URL: {entry.clone_url}")
            source_urls.add(source_url)
            repository = ExternalRepository.from_entry(entry)
            destination = (repository.destination_namespace, repository.destination_name)
            if destination in destinations:
                raise ValueError(
                    "duplicate external destination: "
                    f"{repository.destination_namespace}/{repository.destination_name}"
                )
            destinations.add(destination)
        return self


@dataclass(frozen=True, slots=True)
class ExternalRepository:
    source_provider: str
    source_id: str
    owner: str
    name: str
    full_name: str
    html_url: str
    clone_url: str
    description: str | None
    default_branch: str | None
    private: bool
    archived: bool
    fork: bool
    wiki_clone_url: str | None
    destination_namespace: str
    destination_name: str
    token_env: str | None
    source_username: str
    api_base: str
    releases_enabled: bool
    release_assets_enabled: bool

    @classmethod
    def from_entry(cls, entry: ExternalSourceEntry) -> ExternalRepository:
        parsed = urlsplit(entry.clone_url)
        path = unquote(parsed.path).strip("/").removesuffix(".git")
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2:
            raise ExternalSourcesError(
                f"external source {entry.id!r} clone_url must include an owner and repository"
            )
        owner = "/".join(parts[:-1])
        name = parts[-1]
        web_url = entry.web_url or urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.removesuffix(".git"), "", "")
        )
        default_username = "oauth2" if entry.provider is ExternalProvider.GITLAB else "git"
        default_api_path = "/api/v4" if entry.provider is ExternalProvider.GITLAB else "/api/v1"
        api_base = entry.api_url or urlunsplit(
            (parsed.scheme, parsed.netloc, default_api_path, "", "")
        )
        return cls(
            source_provider=entry.provider.value,
            source_id=entry.id or "",
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            html_url=web_url,
            clone_url=entry.clone_url,
            description=entry.description,
            default_branch=entry.default_branch,
            private=entry.private,
            archived=entry.archived,
            fork=entry.fork,
            wiki_clone_url=entry.wiki_url,
            destination_namespace=entry.destination_namespace,
            destination_name=entry.destination_name or safe_component(name),
            token_env=entry.token_env,
            source_username=entry.git_username or default_username,
            api_base=api_base,
            releases_enabled=entry.releases,
            release_assets_enabled=entry.release_assets,
        )

    def source_token(self) -> str:
        if self.token_env is None:
            return ""
        token = os.environ.get(self.token_env, "")
        if not token:
            raise ExternalSourcesError(
                f"external source {self.source_provider}/{self.source_id} requires "
                f"environment variable {self.token_env}"
            )
        return token


class ExternalSources:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def load(self) -> list[ExternalRepository]:
        if self.path is None:
            return []
        try:
            with self.path.open("rb") as handle:
                payload: Any = tomllib.load(handle)
        except FileNotFoundError as exc:
            raise ExternalSourcesError(f"external sources file not found: {self.path}") from exc
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ExternalSourcesError(f"could not read external sources file: {exc}") from exc
        try:
            document = ExternalSourcesDocument.model_validate(payload)
            return [ExternalRepository.from_entry(entry) for entry in document.repositories]
        except (ValidationError, ExternalSourcesError) as exc:
            raise ExternalSourcesError(f"invalid external sources file: {exc}") from exc
