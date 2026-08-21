from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from githarbor.clients.gitea import DestinationRepository
from githarbor.clients.github import UpstreamRepository
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryStatus
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


class FakeGitea:
    async def ensure_repository(self, **_kwargs: Any) -> DestinationRepository:
        return DestinationRepository(
            "archive", "repo", "https://gitea.test/repo.git", "https://gitea.test/repo"
        )

    async def authenticated_user(self) -> dict[str, str]:
        return {"login": "gitea-user"}

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
    assert await service.sync_repository(repository_id, "test") is False
    git.release.set()
    await service._repository_tasks[repository_id]
    assert git.calls == 1
    with database.session_factory() as session:
        assert session.get(Repository, repository_id).status == RepositoryStatus.ACTIVE.value  # type: ignore[union-attr]


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
