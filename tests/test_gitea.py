from __future__ import annotations

import httpx
import pytest

from githarbor.clients.gitea import (
    DestinationRepository,
    DestinationSafetyError,
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
