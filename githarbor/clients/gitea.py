from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class GiteaError(RuntimeError):
    pass


class DestinationSafetyError(GiteaError):
    pass


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
    def __init__(self, api_base: str, token: str, timeout: int = 30) -> None:
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

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        response = await self._raw_request(method, path, json=json)
        assert response is not None
        return response.json()

    async def _raw_request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        for attempt in range(3):
            try:
                response = await self._client.request(method, path, json=json)
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
