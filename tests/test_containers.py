from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from githarbor.clients.gitea import GiteaPackage
from githarbor.clients.github_packages import (
    GitHubContainerPackage,
    GitHubContainerVersion,
)
from githarbor.config import ContainerImageMode
from githarbor.database import Database
from githarbor.models import Base, ContainerImage, Repository, RepositoryStatus
from githarbor.services.containers import ContainerMirrorService
from githarbor.services.registry import RegistryCredentials, RegistryError


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


def source_version(
    github_id: int, image_digest: str, *tags: str, day: int = 1
) -> GitHubContainerVersion:
    timestamp = datetime(2026, 1, day, tzinfo=UTC)
    return GitHubContainerVersion(github_id, image_digest, tags, timestamp, timestamp)


class FakeGitHubPackages:
    def __init__(self, versions: list[GitHubContainerVersion]) -> None:
        self.package = GitHubContainerPackage(10, "project/image", "octocat", 123)
        self.versions = versions

    async def list_for_repository(self, repository_id: int) -> list[GitHubContainerPackage]:
        return [self.package] if repository_id == 123 else []

    async def list_versions(self, package_name: str) -> list[GitHubContainerVersion]:
        assert package_name == self.package.name
        return self.versions


class FakeRegistry:
    def __init__(self) -> None:
        self.tags: dict[str, dict[str, str]] = {}
        self.digests: dict[str, set[str]] = {}
        self.deleted: list[str] = []
        self.fail_digest: str | None = None

    async def estimate_size(self, _reference: str, _credentials: object) -> int:
        return 42

    async def inspect_digest(self, reference: str, _credentials: object) -> str | None:
        if "@" in reference:
            repository, image_digest = reference.rsplit("@", 1)
            return image_digest if image_digest in self.digests.get(repository, set()) else None
        repository, _, tag = reference.rpartition(":")
        return self.tags.get(repository, {}).get(tag)

    async def copy(
        self,
        _source: str,
        destination: str,
        _source_credentials: object,
        _destination_credentials: object,
        expected_digest: str,
    ) -> None:
        if expected_digest == self.fail_digest:
            raise RegistryError("simulated registry rejection")
        if "@" in destination:
            repository, _ = destination.rsplit("@", 1)
        else:
            repository, _, tag = destination.rpartition(":")
            self.tags.setdefault(repository, {})[tag] = expected_digest
        self.digests.setdefault(repository, set()).add(expected_digest)

    async def list_tags(self, repository: str, _credentials: object) -> set[str]:
        return set(self.tags.get(repository, {}))

    async def delete(self, reference: str, _credentials: object) -> None:
        repository, image_digest = reference.rsplit("@", 1)
        self.digests.setdefault(repository, set()).discard(image_digest)
        self.deleted.append(reference)


class FakeGiteaPackages:
    def __init__(self, registry: FakeRegistry) -> None:
        self.registry = registry
        self.linked_repository: str | None = None
        self.ids: dict[tuple[str, str], int] = {}
        self.next_id = 1
        self.deleted_tags: list[str] = []

    async def list_container_versions(self, namespace: str, package: str) -> list[GiteaPackage]:
        repository = f"gitea.test/{namespace}/{package}"
        versions: list[GiteaPackage] = []
        for tag, image_digest in sorted(self.registry.tags.get(repository, {}).items()):
            key = (tag, image_digest)
            if key not in self.ids:
                self.ids[key] = self.next_id
                self.next_id += 1
            versions.append(
                GiteaPackage(
                    self.ids[key],
                    package,
                    tag,
                    "container",
                    42,
                    self.linked_repository,
                )
            )
        return versions

    async def link_container_package(
        self, _namespace: str, _package: str, repository_name: str
    ) -> None:
        self.linked_repository = repository_name

    async def delete_container_version(self, namespace: str, package: str, version: str) -> None:
        repository = f"gitea.test/{namespace}/{package}"
        self.registry.tags.setdefault(repository, {}).pop(version, None)
        self.deleted_tags.append(version)


def database_with_repository(tmp_path: Path) -> tuple[Database, int]:
    path = tmp_path / "state.db"
    database = Database(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    now = datetime.now(UTC)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=123,
            upstream_owner="octocat",
            upstream_name="project",
            upstream_full_name="octocat/project",
            upstream_url="https://github.test/octocat/project",
            clone_url="https://github.test/octocat/project.git",
            kind="owned",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="backups",
            destination_name="project",
            currently_starred=False,
            first_discovered_at=now,
            last_seen_at=now,
        )
        session.add(repository)
        session.flush()
        return database, repository.id


def service(
    database: Database,
    github: FakeGitHubPackages,
    registry: FakeRegistry,
    gitea: FakeGiteaPackages,
    mode: ContainerImageMode,
) -> ContainerMirrorService:
    return ContainerMirrorService(
        database,
        github,  # type: ignore[arg-type]
        gitea,  # type: ignore[arg-type]
        registry,  # type: ignore[arg-type]
        RegistryCredentials("ghcr.io", "octocat", "github-secret"),
        "gitea.test",
        "gitea-secret",
        destination_tls_verify=True,
        image_mode=mode,
    )


@pytest.mark.asyncio
async def test_all_mode_preserves_every_digest_with_a_stable_tag(tmp_path: Path) -> None:
    database, repository_id = database_with_repository(tmp_path)
    first_digest = digest("a")
    second_digest = digest("b")
    github = FakeGitHubPackages(
        [
            source_version(101, first_digest, "1.0.0"),
            source_version(102, second_digest, day=2),
        ]
    )
    registry = FakeRegistry()
    gitea = FakeGiteaPackages(registry)

    warnings = await service(database, github, registry, gitea, ContainerImageMode.ALL).mirror(
        repository_id, 123, "backups", "project", "gitea-user"
    )

    assert warnings == []
    tags = registry.tags["gitea.test/backups/project/image"]
    assert tags["1.0.0"] == first_digest
    assert tags[f"githarbor-preserved-{first_digest.replace(':', '-')}"] == first_digest
    assert tags[f"githarbor-preserved-{second_digest.replace(':', '-')}"] == second_digest
    with database.session_factory() as session:
        assert len(session.query(ContainerImage).all()) == 2


@pytest.mark.asyncio
async def test_latest_mode_keeps_all_tags_on_latest_digest_and_prunes_previous(
    tmp_path: Path,
) -> None:
    database, repository_id = database_with_repository(tmp_path)
    first_digest = digest("a")
    second_digest = digest("b")
    github = FakeGitHubPackages([source_version(101, first_digest, "latest", "1.0.0")])
    registry = FakeRegistry()
    gitea = FakeGiteaPackages(registry)
    mirror = service(database, github, registry, gitea, ContainerImageMode.LATEST)

    assert await mirror.mirror(repository_id, 123, "backups", "project", "user") == []
    github.versions = [
        source_version(101, first_digest, "1.0.0"),
        source_version(102, second_digest, "latest", "2.0.0", day=2),
    ]
    assert await mirror.mirror(repository_id, 123, "backups", "project", "user") == []

    assert registry.tags["gitea.test/backups/project/image"] == {
        "latest": second_digest,
        "2.0.0": second_digest,
    }
    assert gitea.deleted_tags == ["1.0.0"]
    assert registry.deleted == [f"gitea.test/backups/project/image@{first_digest}"]
    with database.session_factory() as session:
        records = session.query(ContainerImage).all()
        assert [record.github_version_id for record in records] == [102]


@pytest.mark.asyncio
async def test_latest_mode_retains_previous_image_when_new_copy_fails(tmp_path: Path) -> None:
    database, repository_id = database_with_repository(tmp_path)
    first_digest = digest("a")
    second_digest = digest("b")
    github = FakeGitHubPackages([source_version(101, first_digest, "latest", "1.0.0")])
    registry = FakeRegistry()
    gitea = FakeGiteaPackages(registry)
    mirror = service(database, github, registry, gitea, ContainerImageMode.LATEST)
    await mirror.mirror(repository_id, 123, "backups", "project", "user")

    github.versions = [source_version(102, second_digest, "latest", "2.0.0", day=2)]
    registry.fail_digest = second_digest
    warnings = await mirror.mirror(repository_id, 123, "backups", "project", "user")

    assert "simulated registry rejection" in warnings[0]
    assert registry.tags["gitea.test/backups/project/image"] == {
        "latest": first_digest,
        "1.0.0": first_digest,
    }
    assert registry.deleted == []


@pytest.mark.asyncio
async def test_latest_mode_does_not_guess_when_literal_latest_is_missing(tmp_path: Path) -> None:
    database, repository_id = database_with_repository(tmp_path)
    github = FakeGitHubPackages([source_version(101, digest("a"), "1.0.0", day=1)])
    registry = FakeRegistry()
    gitea = FakeGiteaPackages(registry)

    warnings = await service(database, github, registry, gitea, ContainerImageMode.LATEST).mirror(
        repository_id, 123, "backups", "project", "user"
    )

    assert warnings == [
        "container package 'project/image' skipped: GitHub has no literal 'latest' tag"
    ]
    assert registry.tags == {}


@pytest.mark.asyncio
async def test_existing_same_digest_tag_is_not_claimed_as_interrupted_work(tmp_path: Path) -> None:
    database, repository_id = database_with_repository(tmp_path)
    image_digest = digest("a")
    github = FakeGitHubPackages([source_version(101, image_digest, "latest")])
    registry = FakeRegistry()
    repository = "gitea.test/backups/project/image"
    registry.tags[repository] = {"latest": image_digest}
    registry.digests[repository] = {image_digest}
    gitea = FakeGiteaPackages(registry)

    warnings = await service(database, github, registry, gitea, ContainerImageMode.LATEST).mirror(
        repository_id, 123, "backups", "project", "user"
    )

    assert warnings == [
        "container package 'project/image' skipped: an unmanaged Gitea container package "
        "already uses this destination name"
    ]
    with database.session_factory() as session:
        assert session.query(ContainerImage).all() == []
