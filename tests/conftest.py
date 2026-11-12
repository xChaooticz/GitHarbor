from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from githarbor.clients.github import UpstreamRepository
from githarbor.config import Settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    names = {name.casefold() for name in Settings.model_fields}
    for name in os.environ:
        if name.casefold() in names:
            monkeypatch.delenv(name)


@pytest.fixture
def upstream() -> UpstreamRepository:
    return UpstreamRepository(
        github_id=123,
        node_id="R_123",
        owner="octo-user",
        name="project",
        full_name="octo-user/project",
        html_url="https://github.example/octo-user/project",
        clone_url="https://github.example/octo-user/project.git",
        description="An example project",
        default_branch="main",
        private=False,
        archived=False,
        fork=False,
        has_wiki=False,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
