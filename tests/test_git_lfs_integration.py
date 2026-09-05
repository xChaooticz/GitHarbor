from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from githarbor.services.git import GitMirror

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("git-lfs") is None,
    reason="git and git-lfs are required for the real repository integration test",
)


def run_git(*arguments: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.asyncio
async def test_mirror_preserves_lfs_objects_from_every_branch(tmp_path: Path) -> None:
    source_work = tmp_path / "source-work"
    source_bare = tmp_path / "source.git"
    destination_bare = tmp_path / "destination.git"
    restored = tmp_path / "restored"
    source_work.mkdir()

    run_git("init", "--bare", str(source_bare))
    run_git("init", "--bare", str(destination_bare))
    run_git("init", "--initial-branch=main", cwd=source_work)
    run_git("config", "user.name", "GitHarbor Test", cwd=source_work)
    run_git("config", "user.email", "githarbor@example.test", cwd=source_work)
    run_git("lfs", "install", "--local", cwd=source_work)
    run_git("lfs", "track", "*.bin", cwd=source_work)

    main_payload = bytes(range(256)) * 128
    (source_work / "main.bin").write_bytes(main_payload)
    run_git("add", ".gitattributes", "main.bin", cwd=source_work)
    run_git("commit", "-m", "Add main LFS object", cwd=source_work)

    run_git("switch", "-c", "archive", cwd=source_work)
    archive_payload = b"archive-only-lfs-object\0" * 2048
    (source_work / "archive.bin").write_bytes(archive_payload)
    run_git("add", "archive.bin", cwd=source_work)
    run_git("commit", "-m", "Add archive LFS object", cwd=source_work)
    run_git("switch", "main", cwd=source_work)
    run_git("remote", "add", "origin", str(source_bare), cwd=source_work)
    run_git("push", "--all", "origin", cwd=source_work)

    mirror = GitMirror(
        timeout_seconds=60,
        lfs_enabled=True,
        cache_path=tmp_path / "git-mirror-cache",
    )
    await mirror.mirror(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="owned-123",
    )
    # A cached second run must update the existing destination remote rather than trying to add it
    # again, and should reuse the LFS objects retained in the bare mirror.
    await mirror.mirror(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="owned-123",
    )

    shutil.rmtree(source_bare)
    clone_env = os.environ.copy()
    clone_env["GIT_LFS_SKIP_SMUDGE"] = "1"
    run_git("clone", "--no-checkout", str(destination_bare), str(restored), env=clone_env)

    run_git("switch", "main", cwd=restored)
    run_git("lfs", "pull", cwd=restored)
    assert (restored / "main.bin").read_bytes() == main_payload

    run_git("switch", "archive", cwd=restored)
    run_git("lfs", "pull", cwd=restored)
    run_git("lfs", "fsck", cwd=restored)
    assert (restored / "archive.bin").read_bytes() == archive_payload
