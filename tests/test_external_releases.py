from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from githarbor.clients.external_releases import ExternalReleaseClient
from githarbor.external_sources import ExternalRepository, ExternalSources


def repository(tmp_path: Path, provider: str = "forgejo") -> ExternalRepository:
    host = "forge.example" if provider == "forgejo" else "gitlab.example"
    path = tmp_path / "sources.toml"
    path.write_text(
        f"""
[[repositories]]
provider = "{provider}"
clone_url = "https://{host}/group/project.git"
destination_namespace = "external"
""",
        encoding="utf-8",
    )
    return ExternalSources(path).load()[0]


@pytest.mark.asyncio
async def test_forgejo_release_metadata_and_declared_assets_are_adapted(
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["authorization"] == "token source-token"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 11,
                    "html_url": "https://forge.example/group/project/releases/tag/v1",
                    "tag_name": "v1",
                    "target_commitish": "main",
                    "name": "Version 1",
                    "body": "Notes",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "id": 12,
                            "name": "project.zip",
                            "size": 4,
                            "browser_download_url": (
                                "https://forge.example/group/project/releases/download/"
                                "v1/project.zip"
                            ),
                        }
                    ],
                }
            ],
        )

    client = ExternalReleaseClient(repository(tmp_path), "source-token", 30, 30)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://forge.example/api/v1/",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "token source-token"},
    )

    releases = await client.list_releases("group/project")
    await client.close()

    assert requests == [("GET", "/api/v1/repos/group/project/releases")]
    assert releases[0].github_id == 11
    assert releases[0].name == "Version 1"
    assert releases[0].assets[0].github_id == 12
    assert releases[0].assets[0].size == 4
    assert releases[0].assets[0].state == "uploaded"


@pytest.mark.asyncio
async def test_forgejo_repository_metadata_supplies_stable_id_and_naming(tmp_path: Path) -> None:
    source = repository(tmp_path)
    assert source.source_id == ""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/group/project"
        return httpx.Response(
            200,
            json={
                "id": 2,
                "full_name": "group/project",
                "html_url": "https://forge.example/group/project",
                "description": "Project description",
                "default_branch": "master",
                "private": False,
                "archived": False,
                "fork": False,
            },
        )

    client = ExternalReleaseClient(source, "", 30, 30)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://forge.example/api/v1/", transport=httpx.MockTransport(handler)
    )

    resolved = await client.get_repository()
    await client.close()

    assert resolved.source_id == "forge.example-2"
    assert resolved.name == "project"
    assert resolved.destination_name == "project"
    assert resolved.description == "Project description"
    assert resolved.default_branch == "master"


@pytest.mark.asyncio
async def test_explicit_identity_does_not_require_provider_id_in_metadata(tmp_path: Path) -> None:
    source = repository(tmp_path)
    source = replace(source, source_id="compatibility-id")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "full_name": "group/project",
                "default_branch": "main",
            },
        )

    client = ExternalReleaseClient(source, "", 30, 30)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://forge.example/api/v1/", transport=httpx.MockTransport(handler)
    )

    resolved = await client.get_repository()
    await client.close()

    assert resolved.source_id == "compatibility-id"


@pytest.mark.asyncio
async def test_gitlab_release_metadata_marks_unknown_size_links_as_unavailable(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.split(b"?", 1)[0] == (
            b"/api/v4/projects/group%2Fproject/releases"
        )
        assert request.headers["private-token"] == "source-token"
        return httpx.Response(
            200,
            headers={"x-next-page": ""},
            json=[
                {
                    "tag_name": "v2",
                    "name": "Version 2",
                    "description": "GitLab notes",
                    "upcoming_release": False,
                    "_links": {"self": "https://gitlab.example/group/project/-/releases/v2"},
                    "assets": {
                        "links": [
                            {
                                "id": 9,
                                "name": "binary",
                                "url": "https://downloads.example/project-v2",
                            }
                        ]
                    },
                }
            ],
        )

    client = ExternalReleaseClient(repository(tmp_path, "gitlab"), "source-token", 30, 30)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://gitlab.example/api/v4/",
        transport=httpx.MockTransport(handler),
        headers={"PRIVATE-TOKEN": "source-token"},
    )

    releases = await client.list_releases("group/project")
    await client.close()

    assert releases[0].tag_name == "v2"
    assert releases[0].body == "GitLab notes"
    assert releases[0].assets[0].state == "size unavailable"


@pytest.mark.asyncio
async def test_forgejo_release_asset_download_checks_declared_size(tmp_path: Path) -> None:
    source = repository(tmp_path)
    client = ExternalReleaseClient(source, "", 30, 30)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://forge.example/api/v1/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"data")),
    )
    payload = {
        "id": 1,
        "tag_name": "v1",
        "assets": [
            {
                "id": 2,
                "name": "asset.bin",
                "size": 4,
                "browser_download_url": "https://forge.example/download/asset.bin",
            }
        ],
    }
    asset = client._from_forgejo(json.loads(json.dumps(payload))).assets[0]
    destination = tmp_path / "asset.bin"

    await client.download_release_asset(asset, destination)
    await client.close()

    assert destination.read_bytes() == b"data"


@pytest.mark.asyncio
async def test_forgejo_cross_origin_asset_is_not_downloadable(tmp_path: Path) -> None:
    client = ExternalReleaseClient(repository(tmp_path), "source-token", 30, 30)
    release = client._from_forgejo(
        {
            "id": 1,
            "tag_name": "v1",
            "assets": [
                {
                    "id": 2,
                    "name": "asset.bin",
                    "size": 4,
                    "browser_download_url": "https://attacker.example/asset.bin",
                }
            ],
        }
    )

    assert release.assets[0].state == "external asset host blocked"
    await client.close()
