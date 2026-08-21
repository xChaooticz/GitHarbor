from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from githarbor.clients.gitea import (
    AttachmentSettings,
    GiteaAttachment,
    GiteaRelease,
)
from githarbor.clients.github import GitHubRelease, GitHubReleaseAsset
from githarbor.services.releases import (
    ReleaseMarker,
    ReleaseMirrorService,
    decode_release_marker,
    encode_release_body,
)


def source_asset(asset_id: int, name: str, content: bytes) -> GitHubReleaseAsset:
    return GitHubReleaseAsset(
        github_id=asset_id,
        api_url=f"https://api.github.test/assets/{asset_id}",
        name=name,
        label=None,
        state="uploaded",
        content_type="application/octet-stream",
        size=len(content),
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def source_release(*assets: GitHubReleaseAsset) -> GitHubRelease:
    return GitHubRelease(
        github_id=101,
        html_url="https://github.test/octocat/project/releases/tag/v1.0.0",
        tag_name="v1.0.0",
        target_commitish="main",
        name="First release",
        body="Release notes.",
        draft=False,
        prerelease=False,
        assets=assets,
    )


class FakeGitHub:
    def __init__(self, releases: list[GitHubRelease], contents: dict[int, bytes]) -> None:
        self.releases = releases
        self.contents = contents
        self.downloads: list[int] = []

    async def list_releases(self, _full_name: str) -> list[GitHubRelease]:
        return self.releases

    async def download_release_asset(self, asset: GitHubReleaseAsset, path: Path) -> None:
        self.downloads.append(asset.github_id)
        path.write_bytes(self.contents[asset.github_id])


class FakeGitea:
    def __init__(self, settings: AttachmentSettings | None) -> None:
        self.settings = settings
        self.releases: dict[int, GiteaRelease] = {}
        self.assets: dict[int, dict[int, GiteaAttachment]] = {}
        self.next_release_id = 1
        self.next_asset_id = 10
        self.uploads = 0
        self.deleted_assets: list[int] = []

    async def attachment_settings(self) -> AttachmentSettings | None:
        return self.settings

    async def list_releases(self, _namespace: str, _name: str) -> list[GiteaRelease]:
        return list(self.releases.values())

    async def create_release(
        self, _namespace: str, _name: str, payload: dict[str, Any]
    ) -> GiteaRelease:
        release = GiteaRelease(self.next_release_id, str(payload["tag_name"]), str(payload["body"]))
        self.releases[release.gitea_id] = release
        self.assets[release.gitea_id] = {}
        self.next_release_id += 1
        return release

    async def update_release(
        self,
        _namespace: str,
        _name: str,
        release_id: int,
        payload: dict[str, Any],
    ) -> GiteaRelease:
        release = GiteaRelease(release_id, str(payload["tag_name"]), str(payload["body"]))
        self.releases[release_id] = release
        return release

    async def list_release_assets(
        self, _namespace: str, _name: str, release_id: int
    ) -> list[GiteaAttachment]:
        return list(self.assets[release_id].values())

    async def upload_release_asset(
        self,
        _namespace: str,
        _name: str,
        release_id: int,
        asset_name: str,
        _content_type: str,
        path: Path,
    ) -> GiteaAttachment:
        self.uploads += 1
        attachment = GiteaAttachment(self.next_asset_id, asset_name, path.stat().st_size)
        self.assets[release_id][attachment.gitea_id] = attachment
        self.next_asset_id += 1
        return attachment

    async def rename_release_asset(
        self,
        _namespace: str,
        _name: str,
        release_id: int,
        attachment_id: int,
        asset_name: str,
    ) -> GiteaAttachment:
        previous = self.assets[release_id][attachment_id]
        attachment = GiteaAttachment(previous.gitea_id, asset_name, previous.size)
        self.assets[release_id][attachment_id] = attachment
        return attachment

    async def delete_release_asset(
        self, _namespace: str, _name: str, release_id: int, attachment_id: int
    ) -> None:
        self.deleted_assets.append(attachment_id)
        self.assets[release_id].pop(attachment_id)


def test_release_marker_round_trip_and_invalid_marker() -> None:
    marker = ReleaseMarker(42, {})
    body = encode_release_body("Notes", marker)
    assert body.startswith("Notes\n\n<!-- githarbor-release:")
    assert decode_release_marker(body) == marker
    assert decode_release_marker("<!-- githarbor-release:not-base64 -->") is None


@pytest.mark.asyncio
async def test_release_assets_are_idempotent_and_oversized_assets_warn() -> None:
    small_content = b"safe asset"
    small = source_asset(201, "small.zip", small_content)
    large = source_asset(202, "large.iso", b"x" * (1024 * 1024 + 1))
    github = FakeGitHub([source_release(small, large)], {201: small_content})
    gitea = FakeGitea(AttachmentSettings(True, "", 1, 5))
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    warnings = await service.mirror("octocat/project", "archive", "project")
    assert len(warnings) == 1
    assert "exceeds Gitea's advertised 1.0 MiB limit" in warnings[0]
    assert github.downloads == [201]
    assert gitea.uploads == 1

    warnings = await service.mirror("octocat/project", "archive", "project")
    assert len(warnings) == 1
    assert github.downloads == [201]
    assert gitea.uploads == 1
    marker = decode_release_marker(gitea.releases[1].body)
    assert marker is not None
    assert marker.assets[201].name == "small.zip"


@pytest.mark.asyncio
async def test_removed_source_asset_is_deleted_only_with_ownership_marker() -> None:
    content = b"asset"
    asset = source_asset(201, "asset.zip", content)
    github = FakeGitHub([source_release(asset)], {201: content})
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]
    await service.mirror("octocat/project", "archive", "project")

    github.releases = [source_release()]
    assert await service.mirror("octocat/project", "archive", "project") == []
    assert gitea.deleted_assets == [10]
    assert gitea.assets[1] == {}


@pytest.mark.asyncio
async def test_unmanaged_release_tag_collision_is_reported_without_overwrite() -> None:
    github = FakeGitHub([source_release()], {})
    gitea = FakeGitea(None)
    gitea.releases[99] = GiteaRelease(99, "v1.0.0", "Created manually")
    gitea.assets[99] = {}
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    warnings = await service.mirror("octocat/project", "archive", "project")
    assert warnings == [
        "release 'v1.0.0' skipped: an unmanaged Gitea release already uses that tag"
    ]
    assert gitea.releases[99].body == "Created manually"
