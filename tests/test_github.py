from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from githarbor.clients.github import GitHubClient, GitHubError


def payload(repo_id: int, owner: str, name: str) -> dict[str, object]:
    return {
        "id": repo_id,
        "node_id": f"R_{repo_id}",
        "owner": {"login": owner},
        "name": name,
        "full_name": f"{owner}/{name}",
        "html_url": f"https://github.test/{owner}/{name}",
        "clone_url": f"https://github.test/{owner}/{name}.git",
        "description": f"The {name} repository",
        "default_branch": "main",
        "private": False,
        "archived": False,
        "fork": False,
        "has_wiki": True,
    }


@pytest.mark.asyncio
async def test_owned_discovery_filters_owner_and_paginates() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 5:
            raise AssertionError(f"pagination did not terminate: {request.url}")
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octocat"})
        if request.url.path == "/user/repos" and request.url.params.get("page") == "2":
            return httpx.Response(200, json=[payload(2, "octocat", "two")])
        if request.url.path == "/user/repos":
            return httpx.Response(
                200,
                json=[payload(1, "octocat", "one"), payload(99, "an-org", "ignored")],
                headers={"Link": '<https://api.github.test/user/repos?page=2>; rel="next"'},
            )
        return httpx.Response(404)

    client = GitHubClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/", transport=httpx.MockTransport(handler)
    )
    result = await client.list_owned()
    await client.close()
    assert [item.github_id for item in result] == [1, 2]
    assert calls == 3


@pytest.mark.asyncio
async def test_starred_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat/starred"
        return httpx.Response(200, json=[payload(4, "someone", "project")])

    client = GitHubClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/", transport=httpx.MockTransport(handler)
    )
    result = await client.list_starred()
    await client.close()
    assert result[0].full_name == "someone/project"
    assert result[0].description == "The project repository"
    assert result[0].has_wiki is True
    assert result[0].wiki_clone_url == "https://github.test/someone/project.wiki.git"


@pytest.mark.asyncio
async def test_owned_discovery_rejects_token_user_mismatch() -> None:
    client = GitHubClient("https://api.github.test", "secret", "expected")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"login": "other"})
        ),
    )
    with pytest.raises(GitHubError, match="must match"):
        await client.list_owned()
    await client.close()


def test_fixture_is_json_serializable() -> None:
    json.dumps(payload(1, "a", "b"))


@pytest.mark.asyncio
async def test_release_listing_and_verified_asset_download(tmp_path: Path) -> None:
    content = b"release asset contents"
    digest = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/project/releases":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "html_url": "https://github.test/octocat/project/releases/tag/v1",
                        "tag_name": "v1",
                        "target_commitish": "main",
                        "name": "Version 1",
                        "body": "Notes",
                        "draft": False,
                        "prerelease": False,
                        "assets": [
                            {
                                "id": 20,
                                "url": "https://api.github.test/assets/20",
                                "name": "project.zip",
                                "state": "uploaded",
                                "content_type": "application/zip",
                                "size": len(content),
                                "digest": f"sha256:{digest}",
                            }
                        ],
                    }
                ],
            )
        if request.url.path == "/assets/20":
            assert request.headers["accept"] == "application/octet-stream"
            return httpx.Response(200, content=content)
        return httpx.Response(404)

    client = GitHubClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/", transport=httpx.MockTransport(handler)
    )
    releases = await client.list_releases("octocat/project")
    destination = tmp_path / "asset"
    await client.download_release_asset(releases[0].assets[0], destination)
    await client.close()

    assert releases[0].tag_name == "v1"
    assert destination.read_bytes() == content


@pytest.mark.asyncio
async def test_latest_release_and_missing_latest_release() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/project/releases/latest":
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "html_url": "https://github.test/octocat/project/releases/tag/v2",
                    "tag_name": "v2",
                    "target_commitish": "main",
                    "name": "Version 2",
                    "body": "Notes",
                    "draft": False,
                    "prerelease": False,
                    "assets": [],
                },
            )
        if request.url.path == "/repos/octocat/empty/releases/latest":
            return httpx.Response(404)
        return httpx.Response(500)

    client = GitHubClient("https://api.github.test", "secret", "octocat")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.github.test/", transport=httpx.MockTransport(handler)
    )
    latest = await client.get_latest_release("octocat/project")
    missing = await client.get_latest_release("octocat/empty")
    await client.close()

    assert latest is not None
    assert latest.github_id == 11
    assert missing is None
