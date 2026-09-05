from __future__ import annotations

from pathlib import Path

import pytest

from githarbor.external_sources import ExternalSources, ExternalSourcesError


def write_sources(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_loads_forgejo_repository_with_explicit_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
version = 1

[[repositories]]
id = "eden-server"
provider = "forgejo"
clone_url = "https://git.eden-emu.dev/eden-emu/eden.git"
wiki_url = "https://git.eden-emu.dev/eden-emu/eden.wiki.git"
destination_namespace = "external-backups"
token_env = "EDEN_FORGEJO_TOKEN"
default_branch = "main"
""",
    )
    monkeypatch.setenv("EDEN_FORGEJO_TOKEN", "source-secret")

    repository = ExternalSources(path).load()[0]

    assert repository.source_provider == "forgejo"
    assert repository.source_id == "eden-server"
    assert repository.full_name == "eden-emu/eden"
    assert repository.destination_name == "eden"
    assert repository.wiki_clone_url == "https://git.eden-emu.dev/eden-emu/eden.wiki.git"
    assert repository.source_username == "git"
    assert repository.source_token() == "source-secret"


def test_missing_wiki_url_is_an_explicit_skip(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
id = "group-project"
provider = "gitlab"
clone_url = "https://gitlab.example/group/subgroup/project.git"
web_url = "https://gitlab.example/group/subgroup/project"
destination_namespace = "external-backups"
destination_name = "group-project"
""",
    )

    repository = ExternalSources(path).load()[0]

    assert repository.owner == "group/subgroup"
    assert repository.name == "project"
    assert repository.wiki_clone_url is None
    assert repository.source_username == "oauth2"
    assert repository.source_token() == ""


def test_rejects_duplicate_destinations(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
id = "one"
provider = "forgejo"
clone_url = "https://forge.example/team/one.git"
destination_namespace = "external"
destination_name = "same"

[[repositories]]
id = "two"
provider = "gitlab"
clone_url = "https://gitlab.example/team/two.git"
destination_namespace = "external"
destination_name = "same"
""",
    )

    with pytest.raises(ExternalSourcesError, match="duplicate external destination"):
        ExternalSources(path).load()


def test_rejects_credentials_in_clone_url(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
id = "unsafe"
provider = "forgejo"
clone_url = "https://user:secret@forge.example/team/project.git"
destination_namespace = "external"
""",
    )

    with pytest.raises(ExternalSourcesError, match="must not contain credentials"):
        ExternalSources(path).load()


def test_requires_configured_token_environment_at_sync_time(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
id = "private"
provider = "gitlab"
clone_url = "https://gitlab.example/team/private.git"
destination_namespace = "external"
token_env = "MISSING_PRIVATE_TOKEN"
""",
    )

    repository = ExternalSources(path).load()[0]
    with pytest.raises(ExternalSourcesError, match="MISSING_PRIVATE_TOKEN"):
        repository.source_token()


def test_rejects_cross_origin_api_url(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
id = "unsafe-api"
provider = "forgejo"
clone_url = "https://forge.example/team/project.git"
api_url = "https://attacker.example/api/v1"
destination_namespace = "external"
""",
    )

    with pytest.raises(ExternalSourcesError, match="same origin"):
        ExternalSources(path).load()


def test_rejects_authenticated_cross_origin_wiki_url(tmp_path: Path) -> None:
    path = tmp_path / "external-sources.toml"
    write_sources(
        path,
        """
[[repositories]]
provider = "forgejo"
clone_url = "https://forge.example/team/project.git"
wiki_url = "https://attacker.example/team/project.wiki.git"
destination_namespace = "external"
token_env = "PRIVATE_FORGE_TOKEN"
""",
    )

    with pytest.raises(ExternalSourcesError, match="same origin"):
        ExternalSources(path).load()
