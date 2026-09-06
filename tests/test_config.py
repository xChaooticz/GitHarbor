from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from githarbor.config import ContainerImageMode, ReleaseAssetMode, Settings, parse_interval


def settings_values(tmp_path: Path) -> dict[str, object]:
    return {
        "github_token": "github-secret",
        "github_username": "octocat",
        "gitea_url": "https://gitea.example",
        "gitea_token": "gitea-secret",
        "gitea_owned_namespace": "backups",
        "gitea_starred_namespace": "archive",
        "database_path": tmp_path / "state.db",
    }


@pytest.mark.parametrize(
    ("value", "seconds"), [("30m", 1800), ("6h", 21600), ("1d", 86400), (15, 15)]
)
def test_parse_interval(value: str | int, seconds: int) -> None:
    assert parse_interval(value) == seconds


@pytest.mark.parametrize("value", ["", "0", "tomorrow", "2 weeks", -1])
def test_rejects_invalid_interval(value: str | int) -> None:
    with pytest.raises(ValueError):
        parse_interval(value)


def test_settings_validate_and_hide_tokens(tmp_path: Path) -> None:
    settings = Settings(**settings_values(tmp_path), sync_interval="45m")  # type: ignore[arg-type]
    assert settings.sync_interval == 2700
    assert settings.sync_concurrency == 3
    assert settings.git_cache_retention_days == 30
    assert settings.git_pull_refs_enabled is False
    assert settings.external_sources_file is None
    assert settings.wiki_enabled is True
    assert settings.releases_enabled is True
    assert settings.release_assets_enabled is True
    assert settings.release_asset_mode is ReleaseAssetMode.ALL
    assert settings.packages_enabled is False
    assert settings.container_image_mode is ContainerImageMode.ALL
    assert settings.database_url.endswith("state.db")
    assert "github-secret" not in repr(settings)


def test_external_sources_file_accepts_path_and_blank(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    configured = Settings(  # type: ignore[arg-type]
        **settings_values(tmp_path), external_sources_file=path
    )
    disabled = Settings(  # type: ignore[arg-type]
        **settings_values(tmp_path), external_sources_file=""
    )

    assert configured.external_sources_file == path
    assert disabled.external_sources_file is None


def test_settings_accept_feature_flags_and_latest_asset_mode(tmp_path: Path) -> None:
    settings = Settings(
        **settings_values(tmp_path),
        wiki_enabled=False,
        releases_enabled=False,
        release_assets_enabled=False,
        release_asset_mode="LATEST",
    )  # type: ignore[arg-type]
    assert settings.wiki_enabled is False
    assert settings.releases_enabled is False
    assert settings.release_assets_enabled is False
    assert settings.release_asset_mode is ReleaseAssetMode.LATEST


def test_admin_actions_require_a_separate_confirmation_token(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ADMIN_ACTIONS_TOKEN"):
        Settings(**settings_values(tmp_path), admin_actions_enabled=True)  # type: ignore[arg-type]

    settings = Settings(
        **settings_values(tmp_path),
        admin_actions_enabled=True,
        admin_actions_token="admin-secret",
    )  # type: ignore[arg-type]
    assert settings.admin_actions_available is True
    assert "admin-secret" not in repr(settings)


def test_settings_reject_invalid_release_asset_mode(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(**settings_values(tmp_path), release_asset_mode="newest-three")  # type: ignore[arg-type]


def test_package_mirroring_uses_required_github_token(tmp_path: Path) -> None:
    settings = Settings(
        **settings_values(tmp_path),
        packages_enabled=True,
        container_image_mode="LATEST",
    )  # type: ignore[arg-type]
    assert settings.container_image_mode is ContainerImageMode.LATEST
    assert settings.github_token.get_secret_value() == "github-secret"
    assert "github_packages_token" not in Settings.model_fields


def test_package_mirroring_rejects_gitea_subpath(tmp_path: Path) -> None:
    values = settings_values(tmp_path)
    values["gitea_url"] = "https://gitea.example/subpath"
    with pytest.raises(ValidationError, match="must be an instance root"):
        Settings(
            **values,
            packages_enabled=True,
        )  # type: ignore[arg-type]


def test_settings_require_tokens(tmp_path: Path) -> None:
    values = settings_values(tmp_path)
    del values["github_token"]
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


def test_rejects_unsafe_namespace(tmp_path: Path) -> None:
    values = settings_values(tmp_path)
    values["gitea_owned_namespace"] = "../../bad"
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 33])
def test_rejects_unsafe_sync_concurrency(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**settings_values(tmp_path), sync_concurrency=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, 3651])
def test_rejects_unsafe_cache_retention(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[arg-type]
            **settings_values(tmp_path), git_cache_retention_days=value
        )
