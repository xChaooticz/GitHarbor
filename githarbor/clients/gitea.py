from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class GiteaError(RuntimeError):
    pass


class DestinationSafetyError(GiteaError):
    pass


class GiteaAssetTooLarge(GiteaError):
    pass


class GiteaAssetUploadError(GiteaError):
    pass


@dataclass(frozen=True, slots=True)
class AttachmentSettings:
    enabled: bool
    allowed_types: str
    max_size_mebibytes: int
    max_files: int

    @property
    def max_size_bytes(self) -> int | None:
        if self.max_size_mebibytes <= 0:
            return None
        return self.max_size_mebibytes * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GiteaAttachment:
    gitea_id: int
    name: str
    size: int

    @classmethod
    def from_gitea(cls, data: dict[str, Any]) -> GiteaAttachment:
        return cls(gitea_id=int(data["id"]), name=str(data["name"]), size=int(data["size"]))


@dataclass(frozen=True, slots=True)
class GiteaRelease:
    gitea_id: int
    tag_name: str
    body: str

    @classmethod
    def from_gitea(cls, data: dict[str, Any]) -> GiteaRelease:
        return cls(
            gitea_id=int(data["id"]),
            tag_name=str(data["tag_name"]),
            body=str(data.get("body") or ""),
        )


@dataclass(frozen=True, slots=True)
class GiteaPackage:
    gitea_id: int
    name: str
    version: str
    package_type: str
    size: int
    repository_name: str | None

    @classmethod
    def from_gitea(cls, data: dict[str, Any]) -> GiteaPackage:
        repository = data.get("repository")
        return cls(
            gitea_id=int(data["id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            package_type=str(data["type"]),
            size=int(data.get("size") or 0),
            repository_name=(
                str(repository["name"])
                if isinstance(repository, dict) and repository.get("name")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class DestinationRepository:
    namespace: str
    name: str
    clone_url: str
    html_url: str

    @property
    def wiki_clone_url(self) -> str:
        parsed = urlsplit(self.clone_url)
        path = parsed.path.removesuffix(".git") + ".wiki.git"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def management_marker(github_id: int, kind: str) -> str:
    return f"GitHarbor managed; github-id:{github_id}; kind:{kind}"


class GiteaClient:
    def __init__(
        self, api_base: str, token: str, timeout: int = 30, asset_timeout: int = 3600
    ) -> None:
        self._asset_timeout = asset_timeout
        self._client = httpx.AsyncClient(
            base_url=f"{api_base.rstrip('/')}/",
            timeout=timeout,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
                "User-Agent": "GitHarbor/0.1",
            },
        )
        self._authenticated_login: str | None = None
        self._organization_cache: dict[str, bool] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def authenticated_user(self) -> dict[str, Any]:
        data = await self._request("GET", "user")
        if not isinstance(data, dict) or not data.get("login"):
            raise GiteaError("Gitea returned an invalid authenticated user response")
        self._authenticated_login = str(data["login"])
        return data

    async def enable_wiki(self, namespace: str, name: str) -> None:
        payload = await self._request("PATCH", f"repos/{namespace}/{name}", json={"has_wiki": True})
        if not isinstance(payload, dict) or not payload.get("has_wiki"):
            raise GiteaError(
                f"Gitea did not enable the wiki for {namespace}/{name}; "
                "check whether the wiki repository unit is globally available"
            )

    async def attachment_settings(self) -> AttachmentSettings | None:
        response = await self._raw_request("GET", "settings/attachment", allow_not_found=True)
        if response is None:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            raise GiteaError("Gitea returned invalid attachment settings")
        return AttachmentSettings(
            enabled=bool(payload.get("enabled", True)),
            allowed_types=str(payload.get("allowed_types") or ""),
            max_size_mebibytes=int(payload.get("max_size") or 0),
            max_files=int(payload.get("max_files") or 0),
        )

    async def list_releases(self, namespace: str, name: str) -> list[GiteaRelease]:
        releases: list[GiteaRelease] = []
        page = 1
        while True:
            payload = await self._request(
                "GET",
                f"repos/{namespace}/{name}/releases",
                params={"page": str(page), "limit": "50"},
            )
            if not isinstance(payload, list):
                raise GiteaError("Gitea returned an invalid release list")
            page_releases = [
                GiteaRelease.from_gitea(item) for item in payload if isinstance(item, dict)
            ]
            releases.extend(page_releases)
            if len(payload) < 50:
                return releases
            page += 1

    async def create_release(
        self, namespace: str, name: str, payload: dict[str, Any]
    ) -> GiteaRelease:
        data = await self._request("POST", f"repos/{namespace}/{name}/releases", json=payload)
        if not isinstance(data, dict):
            raise GiteaError("Gitea returned an invalid release creation response")
        return GiteaRelease.from_gitea(data)

    async def update_release(
        self, namespace: str, name: str, release_id: int, payload: dict[str, Any]
    ) -> GiteaRelease:
        data = await self._request(
            "PATCH", f"repos/{namespace}/{name}/releases/{release_id}", json=payload
        )
        if not isinstance(data, dict):
            raise GiteaError("Gitea returned an invalid release update response")
        return GiteaRelease.from_gitea(data)

    async def list_release_assets(
        self, namespace: str, name: str, release_id: int
    ) -> list[GiteaAttachment]:
        data = await self._request("GET", f"repos/{namespace}/{name}/releases/{release_id}/assets")
        if not isinstance(data, list):
            raise GiteaError("Gitea returned an invalid release attachment list")
        return [GiteaAttachment.from_gitea(item) for item in data if isinstance(item, dict)]

    async def upload_release_asset(
        self,
        namespace: str,
        name: str,
        release_id: int,
        asset_name: str,
        content_type: str,
        path: Path,
    ) -> GiteaAttachment:
        endpoint = f"repos/{namespace}/{name}/releases/{release_id}/assets"
        for attempt in range(3):
            try:
                with path.open("rb") as handle:
                    response = await self._client.post(
                        endpoint,
                        params={"name": asset_name},
                        files={"attachment": (asset_name, handle, content_type)},
                        timeout=self._asset_timeout,
                    )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GiteaAssetUploadError(
                        f"Gitea asset upload failed: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 413:
                raise GiteaAssetTooLarge("Gitea or its reverse proxy returned HTTP 413")
            if response.is_error:
                raise GiteaAssetUploadError(
                    f"Gitea rejected the release asset with HTTP {response.status_code}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise GiteaAssetUploadError("Gitea returned an invalid asset upload response")
            return GiteaAttachment.from_gitea(payload)
        raise AssertionError("unreachable")

    async def rename_release_asset(
        self,
        namespace: str,
        name: str,
        release_id: int,
        attachment_id: int,
        asset_name: str,
    ) -> GiteaAttachment:
        payload = await self._request(
            "PATCH",
            f"repos/{namespace}/{name}/releases/{release_id}/assets/{attachment_id}",
            json={"name": asset_name},
        )
        if not isinstance(payload, dict):
            raise GiteaError("Gitea returned an invalid attachment update response")
        return GiteaAttachment.from_gitea(payload)

    async def delete_release_asset(
        self, namespace: str, name: str, release_id: int, attachment_id: int
    ) -> None:
        await self._empty_request(
            "DELETE",
            f"repos/{namespace}/{name}/releases/{release_id}/assets/{attachment_id}",
        )

    async def list_container_versions(self, namespace: str, package: str) -> list[GiteaPackage]:
        versions: list[GiteaPackage] = []
        page = 1
        package_path = quote(package, safe="")
        while True:
            response = await self._raw_request(
                "GET",
                f"packages/{quote(namespace, safe='')}/container/{package_path}",
                params={"page": str(page), "limit": "50"},
                allow_not_found=True,
            )
            if response is None:
                return []
            payload = response.json()
            if not isinstance(payload, list):
                raise GiteaError("Gitea returned an invalid container package version list")
            page_versions = [
                GiteaPackage.from_gitea(item) for item in payload if isinstance(item, dict)
            ]
            versions.extend(page_versions)
            if len(payload) < 50:
                return versions
            page += 1

    async def link_container_package(
        self, namespace: str, package: str, repository_name: str
    ) -> None:
        await self._empty_request(
            "POST",
            f"packages/{quote(namespace, safe='')}/container/{quote(package, safe='')}"
            f"/-/link/{quote(repository_name, safe='')}",
        )

    async def delete_container_version(self, namespace: str, package: str, version: str) -> None:
        await self._empty_request(
            "DELETE",
            f"packages/{quote(namespace, safe='')}/container/{quote(package, safe='')}"
            f"/{quote(version, safe='')}",
        )

    async def ensure_repository(
        self,
        namespace: str,
        name: str,
        github_id: int,
        kind: str,
        source_full_name: str,
        private: bool,
    ) -> DestinationRepository:
        marker = management_marker(github_id, kind)
        existing = await self._optional_request("GET", f"repos/{namespace}/{name}")
        if existing is not None:
            self._verify_managed(existing, marker, namespace, name)
            return self._destination(existing, namespace, name)

        body = {
            "name": name,
            "description": f"{marker}; source:{source_full_name}",
            "private": private,
            "auto_init": False,
        }
        if await self._is_organization(namespace):
            created = await self._request("POST", f"orgs/{namespace}/repos", json=body)
        else:
            user = await self.authenticated_user()
            if str(user["login"]).casefold() != namespace.casefold():
                raise GiteaError(
                    f"Namespace {namespace!r} is not an organization or the "
                    "authenticated Gitea user"
                )
            created = await self._request("POST", "user/repos", json=body)
        if not isinstance(created, dict):
            raise GiteaError("Gitea returned an invalid repository creation response")
        return self._destination(created, namespace, name)

    async def _is_organization(self, namespace: str) -> bool:
        if namespace not in self._organization_cache:
            org = await self._optional_request("GET", f"orgs/{namespace}")
            self._organization_cache[namespace] = org is not None
        return self._organization_cache[namespace]

    @staticmethod
    def _verify_managed(payload: dict[str, Any], marker: str, namespace: str, name: str) -> None:
        description = str(payload.get("description") or "")
        if marker not in description:
            raise DestinationSafetyError(
                f"Refusing destination {namespace}/{name}: it does not carry the expected "
                "GitHarbor source marker"
            )

    @staticmethod
    def _destination(payload: dict[str, Any], namespace: str, name: str) -> DestinationRepository:
        clone_url = payload.get("clone_url")
        html_url = payload.get("html_url")
        if not clone_url or not html_url:
            raise GiteaError("Gitea repository response omitted clone_url or html_url")
        return DestinationRepository(namespace, name, str(clone_url), str(html_url))

    async def _optional_request(self, method: str, path: str) -> dict[str, Any] | None:
        response = await self._raw_request(method, path, allow_not_found=True)
        if response is None:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            raise GiteaError("Gitea returned an invalid response")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        response = await self._raw_request(method, path, json=json, params=params)
        assert response is not None
        return response.json()

    async def _empty_request(self, method: str, path: str) -> None:
        await self._raw_request(method, path)

    async def _raw_request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        for attempt in range(3):
            try:
                response = await self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GiteaError(f"Gitea request failed: {exc.__class__.__name__}") from exc
                await asyncio.sleep(2**attempt)
                continue
            if allow_not_found and response.status_code == 404:
                return None
            if response.status_code in {502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.is_error:
                raise GiteaError(f"Gitea API returned HTTP {response.status_code}")
            return response
        raise AssertionError("unreachable")
