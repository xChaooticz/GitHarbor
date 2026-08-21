from __future__ import annotations

from datetime import UTC, datetime

import pytest

from githarbor.clients.github import UpstreamRepository


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
        default_branch="main",
        private=False,
        archived=False,
        fork=False,
        has_wiki=False,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
