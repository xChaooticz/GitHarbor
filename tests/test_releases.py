from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from githarbor.clients.gitea import (
    AttachmentSettings,
    GiteaAttachment,
    GiteaRelease,
)
from githarbor.clients.github import GitHubError, GitHubRelease, GitHubReleaseAsset
from githarbor.config import ReleaseAssetMode
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


def source_release(
    *assets: GitHubReleaseAsset,
    release_id: int = 101,
    tag_name: str = "v1.0.0",
    draft: bool = False,
    prerelease: bool = False,
) -> GitHubRelease:
    return GitHubRelease(
        github_id=release_id,
        html_url=f"https://github.test/octocat/project/releases/tag/{tag_name}",
        tag_name=tag_name,
        target_commitish="main",
        name=f"Release {tag_name}",
        body="Release notes.",
        draft=draft,
        prerelease=prerelease,
        assets=assets,
    )


class FakeGitHub:
    def __init__(self, releases: list[GitHubRelease], contents: dict[int, bytes]) -> None:
        self.releases = releases
        self.contents = contents
        self.downloads: list[int] = []
        self.latest_release_id: int | None = None

    async def list_releases(self, _full_name: str) -> list[GitHubRelease]:
        return self.releases

    async def get_latest_release(self, _full_name: str) -> GitHubRelease | None:
        return next(
            (release for release in self.releases if release.github_id == self.latest_release_id),
            None,
        )

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
        self.creates = 0
        self.updates = 0
        self.asset_lists = 0
        self.deleted_assets: list[int] = []

    async def attachment_settings(self) -> AttachmentSettings | None:
        return self.settings

    async def list_releases(self, _namespace: str, _name: str) -> list[GiteaRelease]:
        return [
            replace(release, assets=tuple(self.assets[release_id].values()))
            for release_id, release in self.releases.items()
        ]

    async def create_release(
        self, _namespace: str, _name: str, payload: dict[str, Any]
    ) -> GiteaRelease:
        self.creates += 1
        release = self._release_from_payload(self.next_release_id, payload)
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
        self.updates += 1
        release = self._release_from_payload(release_id, payload)
        self.releases[release_id] = release
        return release

    async def list_release_assets(
        self, _namespace: str, _name: str, release_id: int
    ) -> list[GiteaAttachment]:
        self.asset_lists += 1
        return list(self.assets[release_id].values())

    @staticmethod
    def _release_from_payload(release_id: int, payload: dict[str, Any]) -> GiteaRelease:
        return GiteaRelease(
            release_id,
            str(payload["tag_name"]),
            str(payload["body"]),
            name=str(payload["name"]),
            target_commitish=str(payload.get("target_commitish") or ""),
            draft=bool(payload["draft"]),
            prerelease=bool(payload["prerelease"]),
        )

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
    assert gitea.creates == 1
    assert gitea.updates == 1
    assert gitea.asset_lists == 1

    warnings = await service.mirror("octocat/project", "archive", "project")
    assert len(warnings) == 1
    assert github.downloads == [201]
    assert gitea.uploads == 1
    assert gitea.creates == 1
    assert gitea.updates == 1
    assert gitea.asset_lists == 1
    marker = decode_release_marker(gitea.releases[1].body)
    assert marker is not None
    assert marker.assets[201].name == "small.zip"


@pytest.mark.asyncio
async def test_unchanged_release_metadata_avoids_updates() -> None:
    source = source_release()
    github = FakeGitHub([source], {})
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    await service.mirror("octocat/project", "archive", "project")
    await service.mirror("octocat/project", "archive", "project")
    assert gitea.creates == 1
    assert gitea.updates == 0

    github.releases = [replace(source, name="Renamed release", body="Updated notes.")]
    await service.mirror("octocat/project", "archive", "project")
    assert gitea.updates == 1
    assert gitea.releases[1].name == "Renamed release"


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


@pytest.mark.asyncio
async def test_disabling_asset_mirroring_retains_existing_managed_assets() -> None:
    content = b"asset"
    asset = source_asset(201, "asset.zip", content)
    github = FakeGitHub([source_release(asset)], {201: content})
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]
    await service.mirror("octocat/project", "archive", "project")

    github.releases = [source_release()]
    assert await service.mirror("octocat/project", "archive", "project", mirror_assets=False) == []
    assert gitea.deleted_assets == []
    assert list(gitea.assets[1].values())[0].name == "asset.zip"
    marker = decode_release_marker(gitea.releases[1].body)
    assert marker is not None
    assert marker.assets[201].name == "asset.zip"


@pytest.mark.asyncio
async def test_latest_mode_moves_asset_retention_to_new_latest_release() -> None:
    first_content = b"first asset"
    second_content = b"second asset"
    first_asset = source_asset(201, "first.zip", first_content)
    second_asset = source_asset(202, "second.zip", second_content)
    first = source_release(first_asset, release_id=101, tag_name="v1.0.0")
    second = source_release(second_asset, release_id=102, tag_name="v2.0.0")
    github = FakeGitHub([first], {201: first_content, 202: second_content})
    github.latest_release_id = first.github_id
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    await service.mirror(
        "octocat/project",
        "archive",
        "project",
        asset_mode=ReleaseAssetMode.LATEST,
    )
    assert [asset.name for asset in gitea.assets[1].values()] == ["first.zip"]

    github.releases = [second, first]
    github.latest_release_id = second.github_id
    await service.mirror(
        "octocat/project",
        "archive",
        "project",
        asset_mode=ReleaseAssetMode.LATEST,
    )

    assert gitea.deleted_assets == [10]
    assert gitea.assets[1] == {}
    assert [asset.name for asset in gitea.assets[2].values()] == ["second.zip"]
    assert github.downloads == [201, 202]


@pytest.mark.asyncio
async def test_latest_mode_retains_previous_assets_when_new_latest_fails() -> None:
    first_content = b"first asset"
    first_asset = source_asset(201, "first.zip", first_content)
    first = source_release(first_asset, release_id=101, tag_name="v1.0.0")
    github = FakeGitHub([first], {201: first_content})
    github.latest_release_id = first.github_id
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]
    await service.mirror(
        "octocat/project",
        "archive",
        "project",
        asset_mode=ReleaseAssetMode.LATEST,
    )

    oversized = source_asset(202, "oversized.iso", b"x" * (1024 * 1024 + 1))
    second = source_release(oversized, release_id=102, tag_name="v2.0.0")
    github.releases = [second, first]
    github.latest_release_id = second.github_id
    gitea.settings = AttachmentSettings(True, "", 1, 5)

    warnings = await service.mirror(
        "octocat/project",
        "archive",
        "project",
        asset_mode=ReleaseAssetMode.LATEST,
    )

    assert len(warnings) == 1
    assert "exceeds Gitea's advertised" in warnings[0]
    assert [asset.name for asset in gitea.assets[1].values()] == ["first.zip"]
    assert gitea.assets[2] == {}
    assert gitea.deleted_assets == []


@pytest.mark.asyncio
async def test_latest_mode_mirrors_prerelease_metadata_without_its_assets() -> None:
    content = b"preview"
    asset = source_asset(301, "preview.zip", content)
    prerelease = source_release(asset, release_id=103, tag_name="v3.0.0-rc1", prerelease=True)
    github = FakeGitHub([prerelease], {301: content})
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    await service.mirror(
        "octocat/project",
        "archive",
        "project",
        asset_mode=ReleaseAssetMode.LATEST,
    )

    assert len(gitea.releases) == 1
    assert gitea.assets[1] == {}
    assert github.downloads == []


@pytest.mark.asyncio
async def test_latest_mode_fails_safe_when_latest_endpoint_omits_stable_release() -> None:
    stable = source_release(release_id=101, tag_name="v1.0.0")
    github = FakeGitHub([stable], {})
    gitea = FakeGitea(None)
    service = ReleaseMirrorService(github, gitea)  # type: ignore[arg-type]

    with pytest.raises(GitHubError, match="omitted a listed stable release"):
        await service.mirror(
            "octocat/project",
            "archive",
            "project",
            asset_mode=ReleaseAssetMode.LATEST,
        )
