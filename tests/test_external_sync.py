from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from githarbor.clients.gitea import DestinationRepository
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryKind, RepositoryStatus
from githarbor.services.sync import SyncService


class FakeExternalClient:
    def __init__(self, repository: Any, *_args: Any) -> None:
        self.repository = repository

    async def get_repository(self) -> Any:
        return replace(self.repository, source_id="git.eden-emu.dev-2")

    async def close(self) -> None:
        return None


class EmptyGitHub:
    async def list_owned(self) -> list[Any]:
        return []

    async def list_starred(self) -> list[Any]:
        return []


class RecordingGitea:
    def __init__(self) -> None:
        self.ensure_calls: list[dict[str, Any]] = []
        self.enabled_wikis: list[tuple[str, str]] = []

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

    async def set_default_branch(self, _namespace: str, _name: str, _branch: str) -> None:
        return None

    async def enable_wiki(self, namespace: str, name: str) -> None:
        self.enabled_wikis.append((namespace, name))

    async def initialize_wiki_if_empty(self, _namespace: str, _name: str) -> bool:
        return True


class RecordingGit:
    def __init__(self) -> None:
        self.mirrors: list[dict[str, Any]] = []
        self.wiki_checks: list[tuple[str, str, str]] = []
        self.wiki_mirrors: list[dict[str, Any]] = []
        self.maintenance: list[set[tuple[str, str]]] = []

    async def mirror(self, **kwargs: Any) -> None:
        self.mirrors.append(kwargs)

    async def remote_has_refs(
        self, source_url: str, source_token: str, source_username: str
    ) -> bool:
        self.wiki_checks.append((source_url, source_token, source_username))
        return True

    async def mirror_wiki(self, **kwargs: Any) -> None:
        self.wiki_mirrors.append(kwargs)

    async def maintain_cache(
        self, active_entries: set[tuple[str, str]], _retention_days: int
    ) -> None:
        self.maintenance.append(active_entries)


def settings(tmp_path: Path, sources_file: Path) -> Settings:
    return Settings(
        github_token="github-secret",
        github_username="octocat",
        gitea_url="https://gitea.test",
        gitea_token="gitea-secret",
        gitea_owned_namespace="backups",
        gitea_starred_namespace="archive",
        database_path=tmp_path / "state.db",
        external_sources_file=sources_file,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_wiki", [True, False])
async def test_external_source_mirrors_repo_and_only_an_explicit_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_wiki: bool
) -> None:
    monkeypatch.setattr("githarbor.services.sync.ExternalReleaseClient", FakeExternalClient)
    sources_file = tmp_path / "external-sources.toml"
    wiki_line = (
        'wiki_url = "https://git.eden-emu.dev/eden-emu/eden.wiki.git"' if include_wiki else ""
    )
    sources_file.write_text(
        f"""
[[repositories]]
provider = "forgejo"
clone_url = "https://git.eden-emu.dev/eden-emu/eden.git"
{wiki_line}
destination_namespace = "external-backups"
destination_name = "eden-server"
token_env = "EDEN_TOKEN"
default_branch = "main"
releases = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDEN_TOKEN", "eden-secret")
    database = Database(f"sqlite:///{tmp_path.joinpath('state.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    gitea = RecordingGitea()
    git = RecordingGit()
    service = SyncService(
        settings(tmp_path, sources_file),
        database,
        EmptyGitHub(),  # type: ignore[arg-type]
        gitea,  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
    )

    await service.sync_all("test")

    assert len(git.mirrors) == 1
    assert git.mirrors[0]["source_username"] == "git"
    assert git.mirrors[0]["source_token"] == "eden-secret"
    assert git.mirrors[0]["cache_key"] == "external-forgejo-git.eden-emu.dev-2"
    assert gitea.ensure_calls[0]["source_provider"] == "forgejo"
    assert gitea.ensure_calls[0]["source_id"] == "git.eden-emu.dev-2"
    assert gitea.ensure_calls[0]["github_id"] is None
    with database.session_factory() as session:
        repository = session.scalar(select(Repository))
        assert repository is not None
        assert repository.kind == RepositoryKind.EXTERNAL.value
        assert repository.status == RepositoryStatus.ACTIVE.value

    if include_wiki:
        assert git.wiki_checks == [
            (
                "https://git.eden-emu.dev/eden-emu/eden.wiki.git",
                "eden-secret",
                "git",
            )
        ]
        assert len(git.wiki_mirrors) == 1
        assert gitea.enabled_wikis == [("external-backups", "eden-server")]
        assert git.maintenance == [
            {
                ("external-forgejo-git.eden-emu.dev-2", "repository.git"),
                ("external-forgejo-git.eden-emu.dev-2", "wiki.git"),
            }
        ]
    else:
        assert git.wiki_checks == []
        assert git.wiki_mirrors == []
        assert gitea.enabled_wikis == []
        assert git.maintenance == [{("external-forgejo-git.eden-emu.dev-2", "repository.git")}]
