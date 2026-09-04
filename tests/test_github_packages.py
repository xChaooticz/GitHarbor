from __future__ import annotations

import httpx
import pytest

from githarbor.clients.github_packages import GitHubPackagesClient, GitHubPackagesError


@pytest.mark.asyncio
async def test_packages_are_filtered_to_owned_repository_and_versions_are_parsed() -> None:
    requests: list[str] = []
    digest = f"sha256:{'a' * 64}"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.raw_path.decode())
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octocat"})
        if request.url.path == "/user/packages":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "name": "project/image",
                        "owner": {"login": "OctoCat"},
                        "repository": {"id": 123},
                    },
                    {
                        "id": 12,
                        "name": "other",
                        "owner": {"login": "octocat"},
                        "repository": {"id": 999},
                    },
                    {
                        "id": 13,
                        "name": "foreign",
                        "owner": {"login": "someone-else"},
                        "repository": {"id": 123},
                    },
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 21,
                    "name": digest,
                    "metadata": {"container": {"tags": ["latest", "1.0.0"]}},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                }
            ],
        )

    client = GitHubPackagesClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/", transport=httpx.MockTransport(handler)
    )

    packages = await client.list_for_repository(123)
    versions = await client.list_versions("project/image")
    await client.close()

    assert [package.github_id for package in packages] == [11]
    assert versions[0].digest == digest
    assert versions[0].tags == ("latest", "1.0.0")
    assert requests == [
        "/user",
        "/user/packages?per_page=100&package_type=container",
        "/user/packages/container/project%2Fimage/versions?per_page=100",
    ]


@pytest.mark.asyncio
async def test_github_token_must_belong_to_configured_user() -> None:
    client = GitHubPackagesClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"login": "different-user"})
        ),
    )

    with pytest.raises(GitHubPackagesError, match="different GitHub account"):
        await client.list_for_repository(123)
    await client.close()
