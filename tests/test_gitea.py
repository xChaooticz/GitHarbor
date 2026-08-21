from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from githarbor.clients.gitea import (
    AttachmentSettings,
    DestinationRepository,
    DestinationSafetyError,
    GiteaAssetTooLarge,
    GiteaClient,
    management_marker,
)


def test_management_marker_uses_stable_identity() -> None:
    assert management_marker(123, "starred") == "GitHarbor managed; github-id:123; kind:starred"


def test_destination_wiki_clone_url() -> None:
    repository = DestinationRepository(
        "archive",
        "project",
        "https://gitea.test/archive/project.git",
        "https://gitea.test/archive/project",
    )
    assert repository.wiki_clone_url == "https://gitea.test/archive/project.wiki.git"


@pytest.mark.asyncio
async def test_enable_wiki_updates_repository_unit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/repos/archive/project"
        assert request.content == b'{"has_wiki":true}'
        return httpx.Response(200, json={"has_wiki": True})

    client = GiteaClient("https://gitea.test", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://gitea.test/", transport=httpx.MockTransport(handler)
    )
    await client.enable_wiki("archive", "project")
    await client.close()


@pytest.mark.asyncio
async def test_attachment_settings_convert_mebibytes() -> None:
    client = GiteaClient("https://gitea.test", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://gitea.test/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"enabled": True, "allowed_types": "*/*", "max_size": 25, "max_files": 5},
            )
        ),
    )
    settings = await client.attachment_settings()
    await client.close()

    assert settings == AttachmentSettings(True, "*/*", 25, 5)
    assert settings.max_size_bytes == 25 * 1024 * 1024


@pytest.mark.asyncio
async def test_release_asset_upload_translates_http_413(tmp_path: Path) -> None:
    path = tmp_path / "asset.zip"
    path.write_bytes(b"contents")
    client = GiteaClient("https://gitea.test", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://gitea.test/",
        transport=httpx.MockTransport(lambda request: httpx.Response(413)),
    )
    with pytest.raises(GiteaAssetTooLarge, match="413"):
        await client.upload_release_asset(
            "archive", "project", 1, "asset.zip", "application/zip", path
        )
    await client.close()


def test_destination_safety_accepts_matching_marker() -> None:
    GiteaClient._verify_managed(
        {"description": f"{management_marker(123, 'starred')}; source:a/b"},
        management_marker(123, "starred"),
        "archive",
        "a--b--gh123",
    )


@pytest.mark.parametrize(
    "description",
    ["", "personal repository", management_marker(999, "starred"), management_marker(123, "owned")],
)
def test_destination_safety_rejects_unrelated_repository(description: str) -> None:
    with pytest.raises(DestinationSafetyError, match="Refusing destination"):
        GiteaClient._verify_managed(
            {"description": description},
            management_marker(123, "starred"),
            "archive",
            "a--b--gh123",
        )
