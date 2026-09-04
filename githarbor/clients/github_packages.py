from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx


class GitHubPackagesError(RuntimeError):
    pass


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class GitHubContainerPackage:
    github_id: int
    name: str
    owner: str
    repository_id: int | None

    @classmethod
    def from_github(cls, data: dict[str, Any]) -> GitHubContainerPackage:
        owner = data.get("owner")
        repository = data.get("repository")
        if not isinstance(owner, dict) or not owner.get("login"):
            raise GitHubPackagesError("GitHub package response omitted its owner")
        return cls(
            github_id=int(data["id"]),
            name=str(data["name"]),
            owner=str(owner["login"]),
            repository_id=(
                int(repository["id"])
                if isinstance(repository, dict) and repository.get("id") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class GitHubContainerVersion:
    github_id: int
    digest: str
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_github(cls, data: dict[str, Any]) -> GitHubContainerVersion:
        digest = str(data.get("name") or "").casefold()
        if not _DIGEST.fullmatch(digest):
            raise GitHubPackagesError("GitHub returned an invalid container manifest digest")
        metadata = data.get("metadata")
        container = metadata.get("container") if isinstance(metadata, dict) else None
        tags_payload = container.get("tags") if isinstance(container, dict) else None
        tags = tuple(str(tag) for tag in tags_payload or [])
        return cls(
            github_id=int(data["id"]),
            digest=digest,
            tags=tags,
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise GitHubPackagesError("GitHub package version omitted its timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubPackagesError("GitHub returned an invalid package timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class GitHubPackagesClient:
    def __init__(
        self,
        api_base: str,
        token: str,
        username: str,
        timeout: int = 30,
    ) -> None:
        self.username = username
        self._username_verified = False
        self._client = httpx.AsyncClient(
            base_url=f"{api_base.rstrip('/')}/",
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "GitHarbor/0.5",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_for_repository(self, repository_id: int) -> list[GitHubContainerPackage]:
        await self._verify_username()
        payloads = await self._paginate("user/packages", params={"package_type": "container"})
        packages = [GitHubContainerPackage.from_github(item) for item in payloads]
        return [
            package
            for package in packages
            if package.repository_id == repository_id
            and package.owner.casefold() == self.username.casefold()
        ]

    async def list_versions(self, package_name: str) -> list[GitHubContainerVersion]:
        encoded_name = quote(package_name, safe="")
        payloads = await self._paginate(f"user/packages/container/{encoded_name}/versions")
        return [GitHubContainerVersion.from_github(item) for item in payloads]

    async def _verify_username(self) -> None:
        if self._username_verified:
            return
        response = await self._request("user")
        payload = response.json()
        login = payload.get("login") if isinstance(payload, dict) else None
        if not isinstance(login, str):
            raise GitHubPackagesError("GitHub token response omitted its login")
        if login.casefold() != self.username.casefold():
            raise GitHubPackagesError("GITHUB_TOKEN belongs to a different GitHub account")
        self._username_verified = True

    async def _paginate(
        self, path: str, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        request_params: dict[str, str] | None = {"per_page": "100", **(params or {})}
        visited: set[str] = set()
        while next_url:
            request_key = f"{next_url}|{request_params}"
            if request_key in visited:
                raise GitHubPackagesError("GitHub returned a package pagination loop")
            visited.add(request_key)
            response = await self._request(next_url, request_params)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubPackagesError("GitHub returned an invalid package list")
            items.extend(item for item in payload if isinstance(item, dict))
            next_link = response.links.get("next", {}).get("url")
            next_url = str(next_link) if next_link else None
            request_params = None
        return items

    async def _request(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GitHubPackagesError(
                        f"GitHub Packages request failed: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.is_error:
                raise GitHubPackagesError(
                    f"GitHub Packages API returned HTTP {response.status_code}"
                )
            return response
        raise AssertionError("unreachable")
