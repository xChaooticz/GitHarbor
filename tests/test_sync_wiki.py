from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from githarbor.clients.gitea import DestinationRepository
from githarbor.clients.github import UpstreamRepository
from githarbor.config import Settings
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

    async def ensure_repository(self, **_kwargs: Any) -> DestinationRepository:
        return DestinationRepository(
            "archive",
            "project",
            "https://gitea.test/archive/project.git",
            "https://gitea.test/archive/project",
        )

    async def authenticated_user(self) -> dict[str, str]:
        return {"login": "gitea-user"}

    async def enable_wiki(self, namespace: str, name: str) -> None:
        self.enabled_wikis.append((namespace, name))

    async def list_releases(self, _namespace: str, _name: str) -> list[Any]:
        return []

    async def attachment_settings(self) -> None:
        return None


class RecordingGit:
    def __init__(self, wiki_has_refs: bool) -> None:
        self.wiki_has_refs = wiki_has_refs
        self.primary_mirrors = 0
        self.wiki_mirrors: list[dict[str, Any]] = []

    async def mirror(self, **_kwargs: Any) -> None:
        self.primary_mirrors += 1

    async def remote_has_refs(self, _source_url: str, _source_token: str) -> bool:
        return self.wiki_has_refs

    async def mirror_wiki(self, **kwargs: Any) -> None:
        self.wiki_mirrors.append(kwargs)


class WarningReleaseMirror:
    async def mirror(self, _source: str, _namespace: str, _name: str) -> list[str]:
        return ["release 'v1.0.0' asset 'large.iso' skipped: HTTP 413"]


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
    if wiki_has_refs:
        assert gitea.enabled_wikis == [("archive", "project")]
        assert git.wiki_mirrors[0]["source_url"].endswith("/project.wiki.git")
        assert git.wiki_mirrors[0]["destination_url"].endswith("/project.wiki.git")
    else:
        assert gitea.enabled_wikis == []
        assert git.wiki_mirrors == []


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
