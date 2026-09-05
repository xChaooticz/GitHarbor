from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubReleaseAsset:
    github_id: int
    api_url: str
    name: str
    label: str | None
    state: str
    content_type: str
    size: int
    digest: str | None

    @classmethod
    def from_github(cls, data: dict[str, Any]) -> GitHubReleaseAsset:
        return cls(
            github_id=int(data["id"]),
            api_url=str(data["url"]),
            name=str(data["name"]),
            label=str(data["label"]) if data.get("label") else None,
            state=str(data.get("state") or "uploaded"),
            content_type=str(data.get("content_type") or "application/octet-stream"),
            size=int(data.get("size") or 0),
            digest=str(data["digest"]) if data.get("digest") else None,
        )


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    github_id: int
    html_url: str
    tag_name: str
    target_commitish: str
    name: str
    body: str
    draft: bool
    prerelease: bool
    assets: tuple[GitHubReleaseAsset, ...]

    @classmethod
    def from_github(cls, data: dict[str, Any]) -> GitHubRelease:
        assets = data.get("assets")
        return cls(
            github_id=int(data["id"]),
            html_url=str(data["html_url"]),
            tag_name=str(data["tag_name"]),
            target_commitish=str(data.get("target_commitish") or ""),
            name=str(data.get("name") or data["tag_name"]),
            body=str(data.get("body") or ""),
            draft=bool(data.get("draft", False)),
            prerelease=bool(data.get("prerelease", False)),
            assets=tuple(
                GitHubReleaseAsset.from_github(item)
                for item in assets or []
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True, slots=True)
class UpstreamRepository:
    github_id: int
    node_id: str | None
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
    has_wiki: bool

    @property
    def source_provider(self) -> str:
        return "github"

    @property
    def source_id(self) -> str:
        return str(self.github_id)

    @property
    def source_username(self) -> str:
        return "x-access-token"

    @property
    def wiki_clone_url(self) -> str:
        parsed = urlsplit(self.clone_url)
        path = parsed.path.removesuffix(".git") + ".wiki.git"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @classmethod
    def from_github(cls, data: dict[str, Any]) -> UpstreamRepository:
        return cls(
            github_id=int(data["id"]),
            node_id=data.get("node_id"),
            owner=str(data["owner"]["login"]),
            name=str(data["name"]),
            full_name=str(data["full_name"]),
            html_url=str(data["html_url"]),
            clone_url=str(data["clone_url"]),
            description=str(data["description"]) if data.get("description") else None,
            default_branch=data.get("default_branch"),
            private=bool(data.get("private", False)),
            archived=bool(data.get("archived", False)),
            fork=bool(data.get("fork", False)),
            has_wiki=bool(data.get("has_wiki", False)),
        )


class GitHubClient:
    def __init__(
        self,
        api_base: str,
        token: str,
        username: str,
        timeout: int = 30,
        asset_timeout: int = 3600,
    ) -> None:
        self.username = username
        self._asset_timeout = asset_timeout
        self._client = httpx.AsyncClient(
            base_url=f"{api_base.rstrip('/')}/",
            timeout=timeout,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "GitHarbor/0.1",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def authenticated_user(self) -> dict[str, Any]:
        data = await self._request("GET", "user")
        if not isinstance(data, dict):
            raise GitHubError("GitHub returned an invalid authenticated user response")
        return data

    async def list_owned(self) -> list[UpstreamRepository]:
        user = await self.authenticated_user()
        if str(user.get("login", "")).casefold() != self.username.casefold():
            raise GitHubError(
                "GITHUB_USERNAME must match the GitHub account that owns GITHUB_TOKEN "
                "so private owned repositories can be discovered safely"
            )
        payloads = await self._paginate(
            "user/repos", params={"affiliation": "owner", "visibility": "all", "sort": "full_name"}
        )
        return [
            UpstreamRepository.from_github(item)
            for item in payloads
            if str(item.get("owner", {}).get("login", "")).casefold() == self.username.casefold()
        ]

    async def list_starred(self) -> list[UpstreamRepository]:
        payloads = await self._paginate(f"users/{self.username}/starred")
        return [UpstreamRepository.from_github(item) for item in payloads]

    async def get_repository(self, full_name: str) -> UpstreamRepository:
        if full_name.count("/") != 1:
            raise GitHubError("Invalid GitHub repository name")
        data = await self._request("GET", f"repos/{full_name}")
        if not isinstance(data, dict):
            raise GitHubError("GitHub returned an invalid repository response")
        return UpstreamRepository.from_github(data)

    async def list_releases(self, full_name: str) -> list[GitHubRelease]:
        if full_name.count("/") != 1:
            raise GitHubError("Invalid GitHub repository name")
        payloads = await self._paginate(f"repos/{full_name}/releases")
        return [GitHubRelease.from_github(item) for item in payloads]

    async def get_latest_release(self, full_name: str) -> GitHubRelease | None:
        if full_name.count("/") != 1:
            raise GitHubError("Invalid GitHub repository name")
        response = await self._raw_request(
            "GET", f"repos/{full_name}/releases/latest", allow_not_found=True
        )
        if response.status_code == 404:
            return None
        data = response.json()
        if not isinstance(data, dict):
            raise GitHubError("GitHub returned an invalid latest release response")
        return GitHubRelease.from_github(data)

    async def download_release_asset(self, asset: GitHubReleaseAsset, destination: Path) -> None:
        for attempt in range(3):
            try:
                async with self._client.stream(
                    "GET",
                    asset.api_url,
                    headers={"Accept": "application/octet-stream"},
                    follow_redirects=True,
                    timeout=self._asset_timeout,
                ) as response:
                    if response.status_code in {502, 503, 504} and attempt < 2:
                        continue
                    if response.is_error:
                        raise GitHubError(
                            f"GitHub asset download returned HTTP {response.status_code}"
                        )
                    digest = hashlib.sha256()
                    downloaded = 0
                    with destination.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            downloaded += len(chunk)
                            if downloaded > asset.size:
                                raise GitHubError(
                                    f"GitHub asset {asset.name!r} exceeded its declared size"
                                )
                            handle.write(chunk)
                            digest.update(chunk)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GitHubError(
                        f"GitHub asset download failed: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if downloaded != asset.size:
                raise GitHubError(
                    f"GitHub asset {asset.name!r} downloaded {downloaded} bytes; "
                    f"expected {asset.size}"
                )
            if asset.digest and asset.digest.startswith("sha256:"):
                expected_digest = asset.digest.removeprefix("sha256:").casefold()
                if digest.hexdigest().casefold() != expected_digest:
                    raise GitHubError(f"GitHub asset {asset.name!r} failed SHA-256 verification")
            return
        raise AssertionError("unreachable")

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
                raise GitHubError("GitHub returned a pagination loop")
            visited.add(request_key)
            response = await self._raw_request("GET", next_url, params=request_params)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError("GitHub returned an invalid paginated response")
            items.extend(item for item in payload if isinstance(item, dict))
            next_link = response.links.get("next", {}).get("url")
            next_url = str(next_link) if next_link else None
            # Passing an empty params mapping would strip the query from GitHub's absolute next URL.
            request_params = None
        return items

    async def _request(self, method: str, path: str) -> Any:
        return (await self._raw_request(method, path)).json()

    async def _raw_request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._client.request(method, path, params=params)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GitHubError(f"GitHub request failed: {exc.__class__.__name__}") from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if allow_not_found and response.status_code == 404:
                return response
            if response.is_error:
                reset = response.headers.get("x-ratelimit-reset")
                rate_note = ""
                if reset and response.status_code in {403, 429}:
                    with suppress(ValueError):
                        rate_note = (
                            f"; rate limit resets at {datetime.fromtimestamp(int(reset), UTC)}"
                        )
                raise GitHubError(f"GitHub API returned HTTP {response.status_code}{rate_note}")
            return response
        raise AssertionError("unreachable")
