from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from githarbor.clients.gitea import DestinationRepository
from githarbor.clients.github import UpstreamRepository
from githarbor.config import ReleaseAssetMode, Settings
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryStatus, RunStatus
from githarbor.services.sync import SyncService


class FakeGitHub:
    def __init__(self, upstream: UpstreamRepository) -> None:
        self.upstream = upstream

    async def get_repository(self, _full_name: str) -> UpstreamRepository:
        return self.upstream

    async def list_releases(self, _full_name: str) -> list[Any]:
        return []


class RecordingGitea:
    def __init__(self) -> None:
        self.enabled_wikis: list[tuple[str, str]] = []
        self.initialized_wikis: list[tuple[str, str]] = []
        self.default_branches: list[tuple[str, str, str]] = []
        self.ensure_calls: list[dict[str, Any]] = []

    async def ensure_repository(self, **kwargs: Any) -> DestinationRepository:
        self.ensure_calls.append(kwargs)
        namespace = str(kwargs["namespace"])
        name = str(kwargs["name"])
        return DestinationRepository(
            namespace,
            name,
            f"https://gitea.test/{namespace}/{name}.git",
            f"https://gitea.test/{namespace}/{name}",
        )

    async def authenticated_user(self) -> dict[str, str]:
        return {"login": "gitea-user"}

    async def enable_wiki(self, namespace: str, name: str) -> None:
        self.enabled_wikis.append((namespace, name))

    async def initialize_wiki_if_empty(self, namespace: str, name: str) -> bool:
        self.initialized_wikis.append((namespace, name))
        return True

    async def set_default_branch(self, namespace: str, name: str, branch: str) -> None:
        self.default_branches.append((namespace, name, branch))

    async def list_releases(self, _namespace: str, _name: str) -> list[Any]:
        return []

    async def attachment_settings(self) -> None:
        return None


class RecordingGit:
    def __init__(self, wiki_has_refs: bool) -> None:
        self.wiki_has_refs = wiki_has_refs
        self.primary_mirrors = 0
        self.wiki_ref_checks = 0
        self.wiki_mirrors: list[dict[str, Any]] = []

    async def mirror(self, **_kwargs: Any) -> None:
        self.primary_mirrors += 1

    async def remote_has_refs(self, _source_url: str, _source_token: str) -> bool:
        self.wiki_ref_checks += 1
        return self.wiki_has_refs

    async def mirror_wiki(self, **kwargs: Any) -> None:
        self.wiki_mirrors.append(kwargs)


class FailingWikiGit(RecordingGit):
    async def mirror_wiki(self, **_kwargs: Any) -> None:
        raise RuntimeError("destination wiki endpoint returned HTTP 500")


class WarningReleaseMirror:
    async def mirror(self, _source: str, _namespace: str, _name: str, **_kwargs: Any) -> list[str]:
        return ["release 'v1.0.0' asset 'large.iso' skipped: HTTP 413"]


class ForbiddenReleaseMirror:
    async def mirror(self, *_args: Any, **_kwargs: Any) -> list[str]:
        raise AssertionError("release mirroring should be disabled")


class FailingReleaseMirror:
    async def mirror(self, *_args: Any, **_kwargs: Any) -> list[str]:
        raise RuntimeError("Gitea API returned HTTP 422: repo is empty")


class RecordingReleaseMirror:
    def __init__(self) -> None:
        self.options: tuple[bool, ReleaseAssetMode] | None = None

    async def mirror(
        self,
        _source: str,
        _namespace: str,
        _name: str,
        *,
        mirror_assets: bool,
        asset_mode: ReleaseAssetMode,
    ) -> list[str]:
        self.options = (mirror_assets, asset_mode)
        return []


class RecordingContainerMirror:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, str, str]] = []

    async def mirror(
        self,
        repository_id: int,
        github_repository_id: int,
        namespace: str,
        repository_name: str,
        destination_username: str,
    ) -> list[str]:
        self.calls.append(
            (
                repository_id,
                github_repository_id,
                namespace,
                repository_name,
                destination_username,
            )
        )
        return []


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        github_token="github-secret",
        github_username="octocat",
        gitea_url="https://gitea.test",
        gitea_token="gitea-secret",
        gitea_owned_namespace="backups",
        gitea_starred_namespace="archive",
        database_path=tmp_path / "state.db",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wiki_has_refs", [True, False])
async def test_sync_mirrors_only_populated_wikis(
    tmp_path: Path, upstream: UpstreamRepository, wiki_has_refs: bool
) -> None:
    upstream = replace(upstream, has_wiki=True)
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="project",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    gitea = RecordingGitea()
    git = RecordingGit(wiki_has_refs)
    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        gitea,  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
    )

    assert await service.sync_repository(repository_id, "test") is True
    assert git.primary_mirrors == 1
    assert gitea.default_branches == [("archive", "project", "main")]
    if wiki_has_refs:
        assert gitea.enabled_wikis == [("archive", "project")]
        assert gitea.initialized_wikis == [("archive", "project")]
        assert git.wiki_mirrors[0]["source_url"].endswith("/project.wiki.git")
        assert git.wiki_mirrors[0]["destination_url"].endswith("/project.wiki.git")
    else:
        assert gitea.enabled_wikis == []
        assert gitea.initialized_wikis == []
        assert git.wiki_mirrors == []


@pytest.mark.asyncio
async def test_sync_migrates_legacy_starred_name_and_persists_destination(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="octo-user--project--gh123",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    gitea = RecordingGitea()
    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        gitea,  # type: ignore[arg-type]
        RecordingGit(False),  # type: ignore[arg-type]
    )

    assert await service.sync_repository(repository_id, "test") is True
    assert gitea.ensure_calls[0]["name"] == "octo-user--project"
    assert gitea.ensure_calls[0]["fallback_name"] == "octo-user--project--gh123"
    assert gitea.ensure_calls[0]["source_description"] == "An example project"
    with database.session_factory() as session:
        repository = session.get(Repository, repository_id)
        assert repository is not None
        assert repository.destination_name == "octo-user--project"
        assert repository.destination_url == "https://gitea.test/archive/octo-user--project"


@pytest.mark.asyncio
async def test_release_asset_warning_is_persisted_as_partial_run(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="project",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        RecordingGitea(),  # type: ignore[arg-type]
        RecordingGit(False),  # type: ignore[arg-type]
    )
    service.release_mirror = WarningReleaseMirror()  # type: ignore[assignment]

    assert await service.sync_repository(repository_id, "test") is True
    with database.session_factory() as session:
        repository = session.get(Repository, repository_id)
        assert repository is not None
        assert "large.iso" in (repository.last_warning or "")
        assert repository.status == RepositoryStatus.ACTIVE.value
        assert len(repository.runs) == 1
        assert repository.runs[0].status == RunStatus.PARTIAL.value
    assert service.status()["counts"]["warning"] == 1

    service.release_mirror = RecordingReleaseMirror()  # type: ignore[assignment]
    assert await service.sync_repository(repository_id, "test") is True
    assert service.status()["counts"]["warning"] == 0


@pytest.mark.asyncio
async def test_wiki_failure_marks_repository_and_run_as_error(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    upstream = replace(upstream, has_wiki=True)
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="project",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    git = FailingWikiGit(True)
    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        RecordingGitea(),  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
    )
    service.release_mirror = ForbiddenReleaseMirror()  # type: ignore[assignment]

    assert await service.sync_repository(repository_id, "test") is False
    assert git.primary_mirrors == 1
    with database.session_factory() as session:
        repository = session.get(Repository, repository_id)
        assert repository is not None
        assert repository.status == RepositoryStatus.ERROR.value
        assert "destination wiki endpoint returned HTTP 500" in (repository.last_error or "")
        assert repository.runs[0].status == RunStatus.FAILED.value


@pytest.mark.asyncio
async def test_disabled_wiki_and_release_features_are_skipped(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    upstream = replace(upstream, has_wiki=True)
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="project",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    settings = make_settings(tmp_path).model_copy(
        update={"wiki_enabled": False, "releases_enabled": False}
    )
    gitea = RecordingGitea()
    git = RecordingGit(True)
    service = SyncService(
        settings,
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        gitea,  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
    )
    service.release_mirror = ForbiddenReleaseMirror()  # type: ignore[assignment]

    assert await service.sync_repository(repository_id, "test") is True
    assert git.primary_mirrors == 1
    assert git.wiki_ref_checks == 0
    assert git.wiki_mirrors == []
    assert gitea.enabled_wikis == []


@pytest.mark.asyncio
async def test_release_asset_options_are_passed_to_release_mirroring(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind="starred",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="archive",
            destination_name="project",
            currently_starred=True,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    settings = make_settings(tmp_path).model_copy(
        update={
            "wiki_enabled": False,
            "release_assets_enabled": False,
            "release_asset_mode": ReleaseAssetMode.LATEST,
        }
    )
    release_mirror = RecordingReleaseMirror()
    service = SyncService(
        settings,
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        RecordingGitea(),  # type: ignore[arg-type]
        RecordingGit(False),  # type: ignore[arg-type]
    )
    service.release_mirror = release_mirror  # type: ignore[assignment]

    assert await service.sync_repository(repository_id, "test") is True
    assert release_mirror.options == (False, ReleaseAssetMode.LATEST)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "namespace", "expected_calls"),
    [("owned", "backups", 1), ("starred", "archive", 0)],
)
async def test_package_mirroring_runs_only_for_owned_repositories(
    tmp_path: Path,
    upstream: UpstreamRepository,
    kind: str,
    namespace: str,
    expected_calls: int,
) -> None:
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        repository = Repository(
            github_id=upstream.github_id,
            upstream_owner=upstream.owner,
            upstream_name=upstream.name,
            upstream_full_name=upstream.full_name,
            upstream_url=upstream.html_url,
            clone_url=upstream.clone_url,
            kind=kind,
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace=namespace,
            destination_name="project",
            currently_starred=kind == "starred",
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    settings = make_settings(tmp_path).model_copy(update={"packages_enabled": True})
    container_mirror = RecordingContainerMirror()
    service = SyncService(
        settings,
        database,
        FakeGitHub(upstream),  # type: ignore[arg-type]
        RecordingGitea(),  # type: ignore[arg-type]
        RecordingGit(False),  # type: ignore[arg-type]
        container_mirror,  # type: ignore[arg-type]
    )

    assert await service.sync_repository(repository_id, "test") is True
    assert len(container_mirror.calls) == expected_calls
    if expected_calls:
        assert container_mirror.calls[0][1:] == (
            upstream.github_id,
            "backups",
            "project",
            "gitea-user",
        )
