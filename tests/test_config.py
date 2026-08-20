from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from githarbor.config import Settings, parse_interval


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
    assert settings.database_url.endswith("state.db")
    assert "github-secret" not in repr(settings)


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
