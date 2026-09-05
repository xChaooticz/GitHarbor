from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from githarbor.clients.gitea import DestinationRepository
from githarbor.clients.github import UpstreamRepository
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryStatus, RunStatus, SyncRun
from githarbor.services.sync import SyncService


class FakeGitHub:
    def __init__(self, upstream: UpstreamRepository) -> None:
        self.upstream = upstream

    async def get_repository(self, _full_name: str) -> UpstreamRepository:
        return self.upstream

    async def list_releases(self, _full_name: str) -> list[Any]:
        return []


class PartiallyFailingGitHub:
    async def list_owned(self) -> list[UpstreamRepository]:
        raise RuntimeError("GitHub temporarily unavailable")

    async def list_starred(self) -> list[UpstreamRepository]:
        return []


class DiscoveringGitHub(FakeGitHub):
    def __init__(self, repositories: list[UpstreamRepository]) -> None:
        super().__init__(repositories[0])
        self.repositories = repositories

    async def list_owned(self) -> list[UpstreamRepository]:
        return self.repositories

    async def list_starred(self) -> list[UpstreamRepository]:
        return []


class FakeGitea:
    async def ensure_repository(self, **kwargs: Any) -> DestinationRepository:
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

    async def set_default_branch(self, _namespace: str, _name: str, _branch: str) -> None:
        return None

    async def list_releases(self, _namespace: str, _name: str) -> list[Any]:
        return []

    async def attachment_settings(self) -> None:
        return None


class BlockingGit:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def mirror(self, **_kwargs: Any) -> None:
        self.calls += 1
        self.started.set()
        await self.release.wait()

    async def maintain_cache(
        self, _active_entries: set[tuple[str, str]], _retention_days: int
    ) -> None:
        return None


class ConcurrencyRecordingGit:
    def __init__(self, expected_concurrency: int) -> None:
        self.expected_concurrency = expected_concurrency
        self.active = 0
        self.maximum_active = 0
        self.calls = 0
        self.release = asyncio.Event()
        self.maintenance_calls: list[tuple[set[tuple[str, str]], int]] = []

    async def mirror(self, **_kwargs: Any) -> None:
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == self.expected_concurrency:
            self.release.set()
        try:
            await asyncio.wait_for(self.release.wait(), timeout=2)
            await asyncio.sleep(0)
        finally:
            self.active -= 1

    async def maintain_cache(
        self, active_entries: set[tuple[str, str]], retention_days: int
    ) -> None:
        self.maintenance_calls.append((active_entries, retention_days))


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
async def test_per_repository_sync_is_locked(tmp_path: Path, upstream: UpstreamRepository) -> None:
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

    git = BlockingGit()
    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),
        FakeGitea(),
        git,  # type: ignore[arg-type]
    )
    assert service.start_repository_sync(repository_id, "test") is True
    assert service.start_repository_sync(repository_id, "test") is False
    await asyncio.wait_for(git.started.wait(), timeout=2)
    assert service.status()["counts"] == {
        "owned": 0,
        "starred": 1,
        "external": 0,
        "active": 0,
        "syncing": 1,
        "unavailable": 0,
        "unstarred": 0,
        "error": 0,
    }
    assert await service.sync_repository(repository_id, "test") is False
    git.release.set()
    await service._repository_tasks[repository_id]
    assert git.calls == 1
    assert service.status()["counts"]["syncing"] == 0
    assert service.status()["counts"]["active"] == 1
    with database.session_factory() as session:
        assert session.get(Repository, repository_id).status == RepositoryStatus.ACTIVE.value  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_stop_active_syncs_cancels_work_without_stopping_the_service(
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

    git = BlockingGit()
    service = SyncService(
        make_settings(tmp_path),
        database,
        FakeGitHub(upstream),
        FakeGitea(),
        git,  # type: ignore[arg-type]
    )
    assert service.start_repository_sync(repository_id, "test") is True
    await asyncio.wait_for(git.started.wait(), timeout=2)

    assert await service.stop_active_syncs() == 1
    assert service.is_running is False
    with database.session_factory() as session:
        repository = session.get(Repository, repository_id)
        assert repository is not None
        assert repository.status == RepositoryStatus.ERROR.value
        assert repository.last_error == "Synchronization stopped by an administrator"
        assert repository.runs[0].status == RunStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_failed_discovery_does_not_mark_known_repository_missing(
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
            kind="owned",
            status=RepositoryStatus.ACTIVE.value,
            destination_namespace="backups",
            destination_name="project",
            currently_starred=False,
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(repository)
        session.flush()
        repository_id = repository.id

    service = SyncService(
        make_settings(tmp_path),
        database,
        PartiallyFailingGitHub(),
        FakeGitea(),
        BlockingGit(),  # type: ignore[arg-type]
    )
    await service.sync_all("test")
    with database.session_factory() as session:
        preserved = session.get(Repository, repository_id)
        assert preserved is not None
        assert preserved.status == RepositoryStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_global_sync_honors_repository_concurrency_limit(
    tmp_path: Path, upstream: UpstreamRepository
) -> None:
    repositories = [
        replace(
            upstream,
            github_id=upstream.github_id + index,
            node_id=f"R_{upstream.github_id + index}",
            name=f"project-{index}",
            full_name=f"octo-user/project-{index}",
            html_url=f"https://github.example/octo-user/project-{index}",
            clone_url=f"https://github.example/octo-user/project-{index}.git",
        )
        for index in range(4)
    ]
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    git = ConcurrencyRecordingGit(expected_concurrency=3)
    service = SyncService(
        make_settings(tmp_path),
        database,
        DiscoveringGitHub(repositories),  # type: ignore[arg-type]
        FakeGitea(),
        git,  # type: ignore[arg-type]
    )

    await service.sync_all("test")

    assert git.calls == 4
    assert git.maximum_active == 3
    assert git.maintenance_calls == [
        (
            {(f"owned-{repository.github_id}", "repository.git") for repository in repositories},
            30,
        )
    ]
    with database.session_factory() as session:
        run = session.scalar(select(SyncRun).where(SyncRun.repository_id.is_(None)))
        assert run is not None
        assert run.status == RunStatus.SUCCESS.value
        assert run.succeeded == 4
        assert run.failed == 0
