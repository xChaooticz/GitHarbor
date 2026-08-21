from __future__ import annotations

import json
import re
from collections.abc import Iterable

from sqlalchemy import select

from githarbor.clients.gitea import GiteaClient, GiteaError, GiteaPackage
from githarbor.clients.github_packages import (
    GitHubContainerPackage,
    GitHubContainerVersion,
    GitHubPackagesClient,
)
from githarbor.config import ContainerImageMode
from githarbor.database import Database
from githarbor.models import ContainerImage
from githarbor.services.registry import RegistryCredentials, RegistryError, SkopeoClient

_PACKAGE_NAME = re.compile(r"^[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class ContainerSafetyError(RuntimeError):
    pass


class ContainerMirrorService:
    def __init__(
        self,
        database: Database,
        github: GitHubPackagesClient,
        gitea: GiteaClient,
        registry: SkopeoClient,
        source_credentials: RegistryCredentials,
        destination_registry: str,
        destination_token: str,
        *,
        destination_tls_verify: bool,
        image_mode: ContainerImageMode,
        max_bytes: int = 0,
    ) -> None:
        self.database = database
        self.github = github
        self.gitea = gitea
        self.registry = registry
        self.source_credentials = source_credentials
        self.destination_registry = destination_registry
        self.destination_token = destination_token
        self.destination_tls_verify = destination_tls_verify
        self.image_mode = image_mode
        self.max_bytes = max_bytes

    async def mirror(
        self,
        repository_id: int,
        github_repository_id: int,
        namespace: str,
        repository_name: str,
        destination_username: str,
    ) -> list[str]:
        packages = await self.github.list_for_repository(github_repository_id)
        destination_credentials = RegistryCredentials(
            self.destination_registry,
            destination_username,
            self.destination_token,
            self.destination_tls_verify,
        )
        warnings: list[str] = []
        for package in packages:
            try:
                warnings.extend(
                    await self._mirror_package(
                        repository_id,
                        package,
                        namespace,
                        repository_name,
                        destination_credentials,
                    )
                )
            except (ContainerSafetyError, GiteaError, RegistryError) as exc:
                warnings.append(f"container package {package.name!r} skipped: {exc}")
        return warnings

    async def _mirror_package(
        self,
        repository_id: int,
        package: GitHubContainerPackage,
        namespace: str,
        repository_name: str,
        destination_credentials: RegistryCredentials,
    ) -> list[str]:
        if not _PACKAGE_NAME.fullmatch(package.name) or len(package.name) > 255:
            raise ContainerSafetyError("GitHub returned an unsafe container package name")

        source_versions = await self.github.list_versions(package.name)
        selected, selection_warnings = self._select_versions(package, source_versions)
        if selection_warnings:
            return selection_warnings
        self._validate_source_tags(selected)

        records = self._records(repository_id, package.github_id)
        destination_versions = await self.gitea.list_container_versions(namespace, package.name)
        visible_versions = [
            version for version in destination_versions if version.version != "_upload"
        ]
        if visible_versions and not records:
            raise ContainerSafetyError(
                "an unmanaged Gitea container package already uses this destination name"
            )
        self._verify_link(destination_versions, repository_name)

        source_repository = (
            f"{self.source_credentials.registry}/{package.owner.casefold()}/{package.name}"
        )
        destination_repository = (
            f"{self.destination_registry}/{namespace.casefold()}/{package.name}"
        )
        package_warnings: list[str] = []
        completed: list[ContainerImage] = []
        for version in sorted(selected, key=lambda item: (item.created_at, item.github_id)):
            version_warnings = await self._mirror_version(
                repository_id,
                package,
                version,
                namespace,
                repository_name,
                source_repository,
                destination_repository,
                destination_credentials,
            )
            package_warnings.extend(version_warnings)
            record = self._record(repository_id, version.github_id)
            if record is not None and record.state == "complete" and not version_warnings:
                completed.append(record)

        if (
            self.image_mode is ContainerImageMode.LATEST
            and len(completed) == 1
            and not package_warnings
        ):
            package_warnings.extend(
                await self._cleanup_older_versions(
                    repository_id,
                    package,
                    completed[0],
                    namespace,
                    destination_repository,
                    destination_credentials,
                )
            )
        return package_warnings

    def _select_versions(
        self,
        package: GitHubContainerPackage,
        versions: list[GitHubContainerVersion],
    ) -> tuple[list[GitHubContainerVersion], list[str]]:
        if self.image_mode is ContainerImageMode.ALL:
            return versions, []
        latest = [
            version
            for version in versions
            if any(tag.casefold() == "latest" for tag in version.tags)
        ]
        prefix = f"container package {package.name!r} skipped"
        if not latest:
            return [], [f"{prefix}: GitHub has no literal 'latest' tag"]
        if len(latest) > 1:
            return [], [f"{prefix}: GitHub associated 'latest' with multiple digests"]
        return latest, []

    @staticmethod
    def _validate_source_tags(versions: list[GitHubContainerVersion]) -> None:
        owners: dict[str, str] = {}
        for version in versions:
            local: set[str] = set()
            for tag in version.tags:
                if not _TAG.fullmatch(tag):
                    raise ContainerSafetyError(f"GitHub returned unsafe container tag {tag!r}")
                folded = tag.casefold()
                if folded in local:
                    raise ContainerSafetyError(
                        "GitHub returned container tags that differ only by letter case"
                    )
                local.add(folded)
                previous = owners.get(folded)
                if previous is not None and previous != version.digest:
                    raise ContainerSafetyError(
                        f"GitHub associated tag {tag!r} with multiple container digests"
                    )
                owners[folded] = version.digest

    async def _mirror_version(
        self,
        repository_id: int,
        package: GitHubContainerPackage,
        version: GitHubContainerVersion,
        namespace: str,
        repository_name: str,
        source_repository: str,
        destination_repository: str,
        destination_credentials: RegistryCredentials,
    ) -> list[str]:
        prefix = f"container package {package.name!r} digest {version.digest[:19]!r} skipped"
        tags = list(version.tags)
        if self.image_mode is ContainerImageMode.ALL:
            tags.append(self._preservation_tag(version.digest))
        tags = list(dict.fromkeys(tags))
        tags.sort(key=lambda tag: (tag.casefold() == "latest", tag.casefold()))
        folded_tags = [tag.casefold() for tag in tags]
        if len(folded_tags) != len(set(folded_tags)):
            return [f"{prefix}: source and preservation tags collide by letter case"]

        source_reference = f"{source_repository}@{version.digest}"
        try:
            estimated_size = await self.registry.estimate_size(
                source_reference, self.source_credentials
            )
        except RegistryError as exc:
            return [f"{prefix}: unable to inspect source image: {exc}"]
        if self.max_bytes and estimated_size > self.max_bytes:
            return [
                f"{prefix}: estimated size {self._format_size(estimated_size)} exceeds "
                f"PACKAGE_MAX_BYTES={self._format_size(self.max_bytes)}"
            ]

        record = self._record(repository_id, version.github_id)
        existing_records = self._records(repository_id, package.github_id)
        destination_versions = await self.gitea.list_container_versions(namespace, package.name)
        by_tag = self._versions_by_tag(destination_versions)
        owned = self._owned_version_ids(existing_records)
        planned = self._decode_versions(record.managed_versions) if record is not None else {}

        for tag in tags:
            collision = by_tag.get(tag.casefold())
            if collision is None or collision.gitea_id in owned:
                continue
            if record is not None and record.state == "pending" and planned.get(tag) == 0:
                actual = await self.registry.inspect_digest(
                    f"{destination_repository}:{tag}", destination_credentials
                )
                if actual == version.digest:
                    continue
            return [f"{prefix}: unmanaged Gitea tag {tag!r} already exists"]

        record = self._prepare_record(repository_id, package, version, tags)

        try:
            for tag in tags:
                destination_tag_reference = f"{destination_repository}:{tag}"
                current_digest = await self.registry.inspect_digest(
                    destination_tag_reference, destination_credentials
                )
                if current_digest != version.digest:
                    await self.registry.copy(
                        source_reference,
                        destination_tag_reference,
                        self.source_credentials,
                        destination_credentials,
                        version.digest,
                    )
                verified_digest = await self.registry.inspect_digest(
                    destination_tag_reference, destination_credentials
                )
                if verified_digest != version.digest:
                    raise RegistryError(f"Gitea tag {tag!r} failed digest verification")
        except RegistryError as exc:
            return [f"{prefix}: {exc}"]

        destination_versions = await self.gitea.list_container_versions(namespace, package.name)
        self._verify_link(destination_versions, repository_name)
        visible_versions = [item for item in destination_versions if item.version != "_upload"]
        if visible_versions and any(item.repository_name is None for item in visible_versions):
            await self.gitea.link_container_package(namespace, package.name, repository_name)
            destination_versions = await self.gitea.list_container_versions(namespace, package.name)
            self._verify_link(destination_versions, repository_name, require_link=True)

        by_tag = self._versions_by_tag(destination_versions)
        managed_versions: dict[str, int] = {}
        for tag in tags:
            destination_version = by_tag.get(tag.casefold())
            if destination_version is None:
                return [f"{prefix}: Gitea API did not expose copied tag {tag!r}"]
            managed_versions[tag] = destination_version.gitea_id
        self._complete_record(record.id, managed_versions)
        return []

    async def _cleanup_older_versions(
        self,
        repository_id: int,
        package: GitHubContainerPackage,
        current: ContainerImage,
        namespace: str,
        destination_repository: str,
        destination_credentials: RegistryCredentials,
    ) -> list[str]:
        warnings: list[str] = []
        protected = {tag.casefold() for tag in self._decode_versions(current.managed_versions)}
        old_records = [
            record
            for record in self._records(repository_id, package.github_id)
            if record.id != current.id
        ]
        for record in old_records:
            versions = self._decode_versions(record.managed_versions)
            destination_versions = await self.gitea.list_container_versions(namespace, package.name)
            by_tag = self._versions_by_tag(destination_versions)
            retained: dict[str, int] = {}
            for tag, expected_id in versions.items():
                if tag.casefold() in protected:
                    continue
                destination_version = by_tag.get(tag.casefold())
                if destination_version is None:
                    continue
                if expected_id == 0:
                    try:
                        actual_digest = await self.registry.inspect_digest(
                            f"{destination_repository}:{tag}", destination_credentials
                        )
                    except RegistryError as exc:
                        warnings.append(
                            f"container package {package.name!r} could not verify interrupted "
                            f"managed tag {tag!r}: {exc}"
                        )
                        retained[tag] = expected_id
                        continue
                    if actual_digest != record.destination_digest:
                        warnings.append(
                            f"container package {package.name!r} retained changed Gitea tag "
                            f"{tag!r}; interrupted ownership could not be verified"
                        )
                        retained[tag] = expected_id
                        continue
                if destination_version.gitea_id != expected_id:
                    if expected_id == 0:
                        try:
                            await self.gitea.delete_container_version(namespace, package.name, tag)
                        except GiteaError as exc:
                            warnings.append(
                                f"container package {package.name!r} could not remove old "
                                f"managed tag {tag!r}: {exc}"
                            )
                            retained[tag] = expected_id
                        continue
                    warnings.append(
                        f"container package {package.name!r} retained changed Gitea tag "
                        f"{tag!r}; its GitHarbor ownership record no longer matches"
                    )
                    retained[tag] = expected_id
                    continue
                try:
                    await self.gitea.delete_container_version(namespace, package.name, tag)
                except GiteaError as exc:
                    warnings.append(
                        f"container package {package.name!r} could not remove old managed tag "
                        f"{tag!r}: {exc}"
                    )
                    retained[tag] = expected_id

            if retained:
                self._update_record_versions(record.id, retained)
                continue

            try:
                tags = await self.registry.list_tags(
                    destination_repository, destination_credentials
                )
                references = [
                    tag
                    for tag in tags
                    if await self.registry.inspect_digest(
                        f"{destination_repository}:{tag}", destination_credentials
                    )
                    == record.destination_digest
                ]
                if references:
                    warnings.append(
                        f"container package {package.name!r} retained old digest "
                        f"{record.destination_digest[:19]!r}: unmanaged tags still reference it"
                    )
                    continue
                if (
                    await self.registry.inspect_digest(
                        f"{destination_repository}@{record.destination_digest}",
                        destination_credentials,
                    )
                    is not None
                ):
                    await self.registry.delete(
                        f"{destination_repository}@{record.destination_digest}",
                        destination_credentials,
                    )
            except RegistryError as exc:
                warnings.append(
                    f"container package {package.name!r} could not remove old managed digest "
                    f"{record.destination_digest[:19]!r}: {exc}"
                )
                continue
            self._delete_record(record.id)
        return warnings

    @staticmethod
    def _verify_link(
        versions: Iterable[GiteaPackage], repository_name: str, *, require_link: bool = False
    ) -> None:
        visible = [item for item in versions if item.version != "_upload"]
        linked = {
            item.repository_name.casefold() for item in visible if item.repository_name is not None
        }
        if linked and linked != {repository_name.casefold()}:
            raise ContainerSafetyError(
                "the Gitea package is linked to a different destination repository"
            )
        if require_link and (
            linked != {repository_name.casefold()}
            or any(item.repository_name is None for item in visible)
        ):
            raise ContainerSafetyError("Gitea did not link the container package to its repository")

    @staticmethod
    def _versions_by_tag(versions: Iterable[GiteaPackage]) -> dict[str, GiteaPackage]:
        result: dict[str, GiteaPackage] = {}
        for version in versions:
            if version.version == "_upload":
                continue
            folded = version.version.casefold()
            if folded in result:
                raise ContainerSafetyError(
                    "Gitea returned container tags that differ only by letter case"
                )
            result[folded] = version
        return result

    @staticmethod
    def _owned_version_ids(records: Iterable[ContainerImage]) -> set[int]:
        return {
            gitea_id
            for record in records
            for gitea_id in ContainerMirrorService._decode_versions(
                record.managed_versions
            ).values()
            if gitea_id > 0
        }

    def _records(self, repository_id: int, github_package_id: int) -> list[ContainerImage]:
        with self.database.session_factory() as session:
            return list(
                session.scalars(
                    select(ContainerImage).where(
                        ContainerImage.repository_id == repository_id,
                        ContainerImage.github_package_id == github_package_id,
                    )
                ).all()
            )

    def _record(self, repository_id: int, github_version_id: int) -> ContainerImage | None:
        with self.database.session_factory() as session:
            return session.scalar(
                select(ContainerImage).where(
                    ContainerImage.repository_id == repository_id,
                    ContainerImage.github_version_id == github_version_id,
                )
            )

    def _prepare_record(
        self,
        repository_id: int,
        package: GitHubContainerPackage,
        version: GitHubContainerVersion,
        tags: list[str],
    ) -> ContainerImage:
        with self.database.session_factory.begin() as session:
            record = session.scalar(
                select(ContainerImage).where(
                    ContainerImage.repository_id == repository_id,
                    ContainerImage.github_version_id == version.github_id,
                )
            )
            if record is not None:
                if (
                    record.github_package_id != package.github_id
                    or record.package_name != package.name
                    or record.source_digest != version.digest
                    or record.destination_digest != version.digest
                ):
                    raise ContainerSafetyError(
                        "stored container ownership does not match GitHub's immutable identity"
                    )
                managed_versions = self._decode_versions(record.managed_versions)
                for tag in tags:
                    managed_versions.setdefault(tag, 0)
                record.managed_versions = json.dumps(managed_versions, sort_keys=True)
                record.state = "pending"
                return record
            record = ContainerImage(
                repository_id=repository_id,
                github_package_id=package.github_id,
                github_version_id=version.github_id,
                package_name=package.name,
                source_digest=version.digest,
                destination_digest=version.digest,
                managed_versions=json.dumps(dict.fromkeys(tags, 0), sort_keys=True),
                state="pending",
            )
            session.add(record)
            session.flush()
            return record

    def _complete_record(self, record_id: int, managed_versions: dict[str, int]) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(ContainerImage, record_id)
            if record is None:
                raise ContainerSafetyError("container ownership journal disappeared")
            record.managed_versions = json.dumps(managed_versions, sort_keys=True)
            record.state = "complete"

    def _update_record_versions(self, record_id: int, managed_versions: dict[str, int]) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(ContainerImage, record_id)
            if record is not None:
                record.managed_versions = json.dumps(managed_versions, sort_keys=True)

    def _delete_record(self, record_id: int) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(ContainerImage, record_id)
            if record is not None:
                session.delete(record)

    @staticmethod
    def _decode_versions(value: str) -> dict[str, int]:
        try:
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise TypeError
            return {str(tag): int(gitea_id) for tag, gitea_id in payload.items()}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContainerSafetyError("stored container ownership metadata is invalid") from exc

    @staticmethod
    def _preservation_tag(digest: str) -> str:
        return f"githarbor-preserved-{digest.replace(':', '-')}"

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KiB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MiB"
        return f"{size / (1024 * 1024 * 1024):.1f} GiB"
