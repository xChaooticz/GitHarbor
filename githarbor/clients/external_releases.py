from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from githarbor.clients.github import (
    GitHubError,
    GitHubRelease,
    GitHubReleaseAsset,
)
from githarbor.external_sources import ExternalRepository
from githarbor.services.naming import safe_component


class ExternalReleaseError(GitHubError):
    pass


class ExternalReleaseClient:
    """Adapt Forgejo and GitLab releases to GitHarbor's release-source interface."""

    def __init__(
        self,
        repository: ExternalRepository,
        token: str,
        timeout: int,
        asset_timeout: int,
    ) -> None:
        self.repository = repository
        self._asset_timeout = asset_timeout
        headers = {"Accept": "application/json", "User-Agent": "GitHarbor/0.1"}
        if token:
            if repository.source_provider == "gitlab":
                headers["PRIVATE-TOKEN"] = token
            else:
                headers["Authorization"] = f"token {token}"
        self._client = httpx.AsyncClient(
            base_url=f"{repository.api_base.rstrip('/')}/",
            timeout=timeout,
            headers=headers,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_repository(self) -> ExternalRepository:
        if self.repository.source_provider == "gitlab":
            path = f"projects/{quote(self.repository.full_name, safe='')}"
        else:
            owner, separator, name = self.repository.full_name.rpartition("/")
            if not separator:
                raise ExternalReleaseError("Forgejo repository path has no owner")
            path = f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        response = await self._raw_request("GET", path)
        assert response is not None
        payload = response.json()
        if not isinstance(payload, dict) or (
            payload.get("id") is None and not self.repository.source_id
        ):
            raise ExternalReleaseError("External source returned invalid repository metadata")
        if self.repository.source_provider == "gitlab":
            return self._repository_from_gitlab(payload)
        return self._repository_from_forgejo(payload)

    async def list_releases(self, _full_name: str) -> list[GitHubRelease]:
        if self.repository.source_provider == "gitlab":
            payloads = await self._paginate_gitlab(self._gitlab_releases_path())
            return [self._from_gitlab(item) for item in payloads]
        payloads = await self._paginate_forgejo(self._forgejo_releases_path())
        return [self._from_forgejo(item) for item in payloads]

    async def get_latest_release(self, _full_name: str) -> GitHubRelease | None:
        if self.repository.source_provider == "gitlab":
            path = f"{self._gitlab_releases_path()}/permalink/latest"
        else:
            path = f"{self._forgejo_releases_path()}/latest"
        response = await self._raw_request("GET", path, allow_not_found=True)
        if response is None:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalReleaseError("External source returned an invalid latest release")
        if self.repository.source_provider == "gitlab":
            return self._from_gitlab(payload)
        return self._from_forgejo(payload)

    async def download_release_asset(self, asset: GitHubReleaseAsset, destination: Path) -> None:
        for attempt in range(3):
            try:
                async with self._client.stream(
                    "GET", asset.api_url, follow_redirects=True, timeout=self._asset_timeout
                ) as response:
                    if response.status_code in {502, 503, 504} and attempt < 2:
                        continue
                    if response.is_error:
                        raise ExternalReleaseError(
                            f"External release asset download returned HTTP {response.status_code}"
                        )
                    downloaded = 0
                    with destination.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            downloaded += len(chunk)
                            if downloaded > asset.size:
                                raise ExternalReleaseError(
                                    f"External release asset {asset.name!r} exceeded its "
                                    "declared size"
                                )
                            handle.write(chunk)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ExternalReleaseError(
                        f"External release asset download failed: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if downloaded != asset.size:
                raise ExternalReleaseError(
                    f"External release asset {asset.name!r} downloaded {downloaded} bytes; "
                    f"expected {asset.size}"
                )
            return
        raise AssertionError("unreachable")

    async def _paginate_forgejo(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response = await self._raw_request(
                "GET", path, params={"page": str(page), "limit": "50"}
            )
            assert response is not None
            payload = response.json()
            if not isinstance(payload, list):
                raise ExternalReleaseError("Forgejo returned an invalid release list")
            page_items = [item for item in payload if isinstance(item, dict)]
            items.extend(page_items)
            if len(payload) < 50:
                return items
            page += 1

    async def _paginate_gitlab(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = "1"
        while page:
            response = await self._raw_request(
                "GET", path, params={"page": page, "per_page": "100"}
            )
            assert response is not None
            payload = response.json()
            if not isinstance(payload, list):
                raise ExternalReleaseError("GitLab returned an invalid release list")
            items.extend(item for item in payload if isinstance(item, dict))
            page = response.headers.get("x-next-page", "")
        return items

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        for attempt in range(3):
            try:
                response = await self._client.request(method, path, params=params)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ExternalReleaseError(
                        f"External release API request failed: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if allow_not_found and response.status_code == 404:
                return None
            if response.status_code in {502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.is_error:
                raise ExternalReleaseError(
                    f"External release API returned HTTP {response.status_code}"
                )
            return response
        raise AssertionError("unreachable")

    def _from_forgejo(self, data: dict[str, Any]) -> GitHubRelease:
        release_id = self._integer_id(data.get("id"), str(data.get("tag_name") or ""))
        assets_payload = data.get("assets")
        assets = tuple(
            GitHubReleaseAsset(
                github_id=self._integer_id(item.get("id"), f"{release_id}:{item.get('name')}"),
                api_url=str(item.get("browser_download_url") or item.get("url") or ""),
                name=str(item.get("name") or "asset"),
                label=None,
                state=self._forgejo_asset_state(item),
                content_type=str(item.get("content_type") or "application/octet-stream"),
                size=int(item.get("size") or 0),
                digest=None,
            )
            for item in assets_payload or []
            if isinstance(item, dict) and (item.get("browser_download_url") or item.get("url"))
        )
        return GitHubRelease(
            github_id=release_id,
            html_url=str(data.get("html_url") or self.repository.html_url),
            tag_name=str(data["tag_name"]),
            target_commitish=str(data.get("target_commitish") or ""),
            name=str(data.get("name") or data["tag_name"]),
            body=str(data.get("body") or ""),
            draft=bool(data.get("draft", False)),
            prerelease=bool(data.get("prerelease", False)),
            assets=assets,
        )

    def _repository_from_forgejo(self, data: dict[str, Any]) -> ExternalRepository:
        full_name = str(data.get("full_name") or "")
        owner, separator, name = full_name.rpartition("/")
        if not separator or not owner or not name:
            raise ExternalReleaseError("Forgejo returned an invalid repository full_name")
        return replace(
            self.repository,
            source_id=self._repository_source_id(data.get("id")),
            owner=owner,
            name=name,
            full_name=full_name,
            html_url=str(data.get("html_url") or self.repository.html_url),
            description=(str(data["description"]) if data.get("description") else None),
            default_branch=(str(data["default_branch"]) if data.get("default_branch") else None),
            private=bool(data.get("private", False)),
            archived=bool(data.get("archived", False)),
            fork=bool(data.get("fork", False)),
        )

    def _repository_from_gitlab(self, data: dict[str, Any]) -> ExternalRepository:
        full_name = str(data.get("path_with_namespace") or "")
        owner, separator, name = full_name.rpartition("/")
        if not separator or not owner or not name:
            raise ExternalReleaseError("GitLab returned an invalid path_with_namespace")
        return replace(
            self.repository,
            source_id=self._repository_source_id(data.get("id")),
            owner=owner,
            name=name,
            full_name=full_name,
            html_url=str(data.get("web_url") or self.repository.html_url),
            description=(str(data["description"]) if data.get("description") else None),
            default_branch=(str(data["default_branch"]) if data.get("default_branch") else None),
            private=str(data.get("visibility") or "public") != "public",
            archived=bool(data.get("archived", False)),
            fork=isinstance(data.get("forked_from_project"), dict),
        )

    def _from_gitlab(self, data: dict[str, Any]) -> GitHubRelease:
        tag_name = str(data["tag_name"])
        release_id = self._integer_id(None, tag_name)
        assets_payload = data.get("assets")
        links = assets_payload.get("links") if isinstance(assets_payload, dict) else []
        assets = tuple(
            GitHubReleaseAsset(
                github_id=self._integer_id(item.get("id"), f"{release_id}:{item.get('name')}"),
                api_url=str(item.get("direct_asset_url") or item.get("url") or ""),
                name=str(item.get("name") or "asset"),
                label=None,
                state="size unavailable",
                content_type="application/octet-stream",
                size=0,
                digest=None,
            )
            for item in links or []
            if isinstance(item, dict) and (item.get("direct_asset_url") or item.get("url"))
        )
        return GitHubRelease(
            github_id=release_id,
            html_url=str(data.get("_links", {}).get("self") or self.repository.html_url),
            tag_name=tag_name,
            target_commitish="",
            name=str(data.get("name") or tag_name),
            body=str(data.get("description") or ""),
            draft=bool(data.get("upcoming_release", False)),
            prerelease=False,
            assets=assets,
        )

    def _forgejo_releases_path(self) -> str:
        owner, separator, name = self.repository.full_name.rpartition("/")
        if not separator:
            raise ExternalReleaseError("Forgejo repository path has no owner")
        return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}/releases"

    def _gitlab_releases_path(self) -> str:
        return f"projects/{quote(self.repository.full_name, safe='')}/releases"

    def _integer_id(self, value: Any, fallback: str) -> int:
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        identity = f"{self.repository.source_provider}:{self.repository.source_id}:{fallback}"
        return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") >> 1

    def _repository_source_id(self, provider_id: Any) -> str:
        if self.repository.source_id:
            return self.repository.source_id
        instance = safe_component(urlsplit(self.repository.clone_url).netloc.casefold(), 80)
        return f"{instance}-{provider_id}"

    def _forgejo_asset_state(self, data: dict[str, Any]) -> str:
        if "size" not in data:
            return "size unavailable"
        asset_url = str(data.get("browser_download_url") or data.get("url") or "")
        source = urlsplit(self.repository.clone_url)
        asset = urlsplit(asset_url)
        if (source.scheme.casefold(), source.hostname, source.port) != (
            asset.scheme.casefold(),
            asset.hostname,
            asset.port,
        ):
            return "external asset host blocked"
        return "uploaded"
