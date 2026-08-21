from __future__ import annotations

import base64
import binascii
import json
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from githarbor.clients.gitea import (
    AttachmentSettings,
    GiteaAssetTooLarge,
    GiteaAssetUploadError,
    GiteaAttachment,
    GiteaClient,
    GiteaError,
    GiteaRelease,
)
from githarbor.clients.github import GitHubClient, GitHubError, GitHubRelease, GitHubReleaseAsset

_MARKER_PATTERN = re.compile(r"<!-- githarbor-release:([A-Za-z0-9_-]+) -->\s*$")


@dataclass(frozen=True, slots=True)
class MirroredAsset:
    github_id: int
    gitea_id: int
    name: str
    size: int
    digest: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MirroredAsset:
        return cls(
            github_id=int(data["github_id"]),
            gitea_id=int(data["gitea_id"]),
            name=str(data["name"]),
            size=int(data["size"]),
            digest=str(data["digest"]) if data.get("digest") else None,
        )

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "github_id": self.github_id,
            "gitea_id": self.gitea_id,
            "name": self.name,
            "size": self.size,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class ReleaseMarker:
    github_id: int
    assets: dict[int, MirroredAsset]


def encode_release_body(source_body: str, marker: ReleaseMarker) -> str:
    payload = {
        "version": 1,
        "github_id": marker.github_id,
        "assets": [
            asset.as_dict()
            for asset in sorted(marker.assets.values(), key=lambda item: item.github_id)
        ],
    }
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    separator = "\n\n" if source_body else ""
    return f"{source_body}{separator}<!-- githarbor-release:{encoded} -->"


def decode_release_marker(body: str) -> ReleaseMarker | None:
    match = _MARKER_PATTERN.search(body)
    if match is None:
        return None
    encoded = match.group(1)
    encoded += "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        if not isinstance(payload, dict) or int(payload.get("version", 0)) != 1:
            return None
        assets_payload = payload.get("assets", [])
        if not isinstance(assets_payload, list):
            return None
        assets = {
            asset.github_id: asset
            for item in assets_payload
            if isinstance(item, dict)
            for asset in [MirroredAsset.from_dict(item)]
        }
        return ReleaseMarker(github_id=int(payload["github_id"]), assets=assets)
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


class ReleaseMirrorService:
    def __init__(self, github: GitHubClient, gitea: GiteaClient) -> None:
        self.github = github
        self.gitea = gitea

    async def mirror(self, source_full_name: str, namespace: str, name: str) -> list[str]:
        source_releases = await self.github.list_releases(source_full_name)
        destination_releases = await self.gitea.list_releases(namespace, name)
        attachment_settings = await self.gitea.attachment_settings()
        warnings: list[str] = []

        managed: dict[int, tuple[GiteaRelease, ReleaseMarker]] = {}
        by_tag = {release.tag_name: release for release in destination_releases}
        for release in destination_releases:
            marker = decode_release_marker(release.body)
            if marker is not None:
                managed[marker.github_id] = (release, marker)

        for source in source_releases:
            managed_release = managed.get(source.github_id)
            if managed_release is None:
                collision = by_tag.get(source.tag_name)
                if collision is not None:
                    warnings.append(
                        f"release {source.tag_name!r} skipped: an unmanaged Gitea release "
                        "already uses that tag"
                    )
                    continue
                marker = ReleaseMarker(source.github_id, {})
                destination = await self.gitea.create_release(
                    namespace, name, self._release_payload(source, marker)
                )
            else:
                destination, marker = managed_release
                destination = await self.gitea.update_release(
                    namespace,
                    name,
                    destination.gitea_id,
                    self._release_payload(source, marker),
                )

            updated_assets, asset_warnings = await self._mirror_assets(
                source,
                destination,
                marker.assets,
                attachment_settings,
                namespace,
                name,
            )
            warnings.extend(asset_warnings)
            final_marker = ReleaseMarker(source.github_id, updated_assets)
            await self.gitea.update_release(
                namespace,
                name,
                destination.gitea_id,
                self._release_payload(source, final_marker),
            )

        return warnings

    @staticmethod
    def _release_payload(source: GitHubRelease, marker: ReleaseMarker) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tag_name": source.tag_name,
            "name": source.name,
            "body": encode_release_body(source.body, marker),
            "draft": source.draft,
            "prerelease": source.prerelease,
        }
        if source.target_commitish:
            payload["target_commitish"] = source.target_commitish
        return payload

    async def _mirror_assets(
        self,
        source: GitHubRelease,
        destination: GiteaRelease,
        previous: dict[int, MirroredAsset],
        settings: AttachmentSettings | None,
        namespace: str,
        name: str,
    ) -> tuple[dict[int, MirroredAsset], list[str]]:
        destination_assets = await self.gitea.list_release_assets(
            namespace, name, destination.gitea_id
        )
        by_id = {asset.gitea_id: asset for asset in destination_assets}
        by_name = {asset.name: asset for asset in destination_assets}
        current_source_ids = {asset.github_id for asset in source.assets}
        mirrored = dict(previous)
        warnings: list[str] = []

        for github_id, record in list(mirrored.items()):
            if github_id in current_source_ids:
                continue
            attachment = by_id.get(record.gitea_id)
            if attachment is None:
                mirrored.pop(github_id)
                continue
            if attachment.name != record.name or attachment.size != record.size:
                warnings.append(
                    f"release {source.tag_name!r} retained changed Gitea asset {record.name!r}; "
                    "its GitHarbor ownership record no longer matches"
                )
                mirrored.pop(github_id)
                continue
            try:
                await self.gitea.delete_release_asset(
                    namespace, name, destination.gitea_id, attachment.gitea_id
                )
            except GiteaError as exc:
                warnings.append(
                    f"release {source.tag_name!r} could not remove stale asset "
                    f"{record.name!r}: {exc}"
                )
                continue
            mirrored.pop(github_id)
            by_id.pop(attachment.gitea_id, None)
            by_name.pop(attachment.name, None)

        for asset in source.assets:
            warning = await self._mirror_asset(
                source,
                asset,
                destination,
                mirrored,
                by_id,
                by_name,
                settings,
                namespace,
                name,
            )
            if warning:
                warnings.append(warning)

        return mirrored, warnings

    async def _mirror_asset(
        self,
        source: GitHubRelease,
        asset: GitHubReleaseAsset,
        destination: GiteaRelease,
        mirrored: dict[int, MirroredAsset],
        by_id: dict[int, GiteaAttachment],
        by_name: dict[str, GiteaAttachment],
        settings: AttachmentSettings | None,
        namespace: str,
        name: str,
    ) -> str | None:
        prefix = f"release {source.tag_name!r} asset {asset.name!r} skipped"
        previous = mirrored.get(asset.github_id)
        if previous is not None:
            attachment = by_id.get(previous.gitea_id)
            if attachment is None:
                mirrored.pop(asset.github_id)
            elif attachment.size != asset.size:
                return f"{prefix}: the mapped Gitea attachment size changed"
            else:
                if attachment.name != asset.name:
                    try:
                        attachment = await self.gitea.rename_release_asset(
                            namespace,
                            name,
                            destination.gitea_id,
                            attachment.gitea_id,
                            asset.name,
                        )
                    except GiteaError as exc:
                        return f"{prefix}: Gitea rename failed: {exc}"
                mirrored[asset.github_id] = MirroredAsset(
                    asset.github_id,
                    attachment.gitea_id,
                    asset.name,
                    asset.size,
                    asset.digest,
                )
                return None

        if asset.state != "uploaded":
            return f"{prefix}: GitHub reports state {asset.state!r}"
        if settings is not None and not settings.enabled:
            return f"{prefix}: Gitea reports attachments are disabled"
        maximum = settings.max_size_bytes if settings is not None else None
        if maximum is not None and asset.size > maximum:
            return (
                f"{prefix}: {self._format_size(asset.size)} exceeds Gitea's advertised "
                f"{self._format_size(maximum)} limit"
            )
        collision = by_name.get(asset.name)
        if collision is not None:
            return f"{prefix}: an unmanaged Gitea attachment already uses that name"

        try:
            with tempfile.TemporaryDirectory(prefix="githarbor-release-") as temporary:
                path = Path(temporary) / f"asset-{asset.github_id}"
                await self.github.download_release_asset(asset, path)
                uploaded = await self.gitea.upload_release_asset(
                    namespace,
                    name,
                    destination.gitea_id,
                    asset.name,
                    asset.content_type,
                    path,
                )
        except GiteaAssetTooLarge as exc:
            return f"{prefix}: {exc}"
        except (GitHubError, GiteaAssetUploadError, GiteaError) as exc:
            return f"{prefix}: {exc}"

        if uploaded.size != asset.size:
            with suppress(GiteaError):
                await self.gitea.delete_release_asset(
                    namespace, name, destination.gitea_id, uploaded.gitea_id
                )
            actual_size = uploaded.size
            expected_size = asset.size
            detail = f"Gitea reported {actual_size} bytes after an {expected_size}-byte upload"
            return f"{prefix}: {detail}"

        mirrored[asset.github_id] = MirroredAsset(
            asset.github_id,
            uploaded.gitea_id,
            asset.name,
            asset.size,
            asset.digest,
        )
        by_id[uploaded.gitea_id] = uploaded
        by_name[uploaded.name] = uploaded
        return None

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size / (1024 * 1024):.1f} MiB"
