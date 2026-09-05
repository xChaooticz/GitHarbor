from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from githarbor.services.git import GitError, GitMirror


@pytest.mark.asyncio
async def test_cache_maintenance_collects_active_and_removes_only_expired_managed_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_path = tmp_path / "cache"
    mirror = GitMirror(lfs_enabled=False, cache_path=cache_path)
    active = mirror._cached_mirror_path("owned-1", "repository.git")
    expired = mirror._cached_mirror_path("starred-2", "repository.git")
    assert active is not None and expired is not None
    active.mkdir(parents=True)
    expired.mkdir()
    stale_staging = cache_path / ".githarbor-rebuild-stale"
    stale_staging.mkdir()
    unknown = cache_path / "operator-owned-directory"
    unknown.mkdir()
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(stale_staging, (old, old))
    commands: list[Sequence[str]] = []

    async def execute(
        command: Sequence[str],
        _cwd: Path,
        _env: Mapping[str, str],
        _input_data: bytes | None = None,
    ) -> tuple[int, bytes, bytes]:
        commands.append(command)
        return 0, b"", b""

    monkeypatch.setattr(mirror, "_execute", execute)
    await mirror.maintain_cache({("owned-1", "repository.git")}, retention_days=0)

    assert active.is_dir()
    assert not expired.exists()
    assert not stale_staging.exists()
    assert unknown.is_dir()
    assert mirror.cache_gc_command(active) in commands


@pytest.mark.asyncio
async def test_fetch_failure_keeps_a_healthy_cache_instead_of_rebuilding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mirror = GitMirror(lfs_enabled=False, cache_path=tmp_path / "cache")
    cached = mirror._cached_mirror_path("owned-1", "repository.git")
    assert cached is not None
    cached.mkdir(parents=True)

    async def fail_fetch(command: Sequence[str], *_arguments: object, **_kwargs: object) -> None:
        if "fetch" in command:
            raise GitError("network unavailable")

    monkeypatch.setattr(mirror, "_run", fail_fetch)
    monkeypatch.setattr(mirror, "_is_bare_mirror", AsyncMock(return_value=True))
    monkeypatch.setattr(mirror, "_is_healthy_mirror", AsyncMock(return_value=True))
    rebuild = AsyncMock()
    monkeypatch.setattr(mirror, "_replace_cached_mirror", rebuild)

    with pytest.raises(GitError, match="network unavailable"):
        await mirror.mirror("source", "", "destination", "", "", cache_key="owned-1")

    rebuild.assert_not_awaited()
