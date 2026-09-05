from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from githarbor.services.git import GitMirror

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def run_git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_github_pull_refs_are_preserved_outside_giteas_reserved_namespace(
    tmp_path: Path,
) -> None:
    source_work = tmp_path / "source-work"
    source_bare = tmp_path / "source.git"
    destination_bare = tmp_path / "destination.git"
    source_work.mkdir()

    run_git("init", "--bare", str(source_bare))
    run_git("init", "--bare", str(destination_bare))
    run_git("init", "--initial-branch=main", cwd=source_work)
    run_git("config", "user.name", "GitHarbor Test", cwd=source_work)
    run_git("config", "user.email", "githarbor@example.test", cwd=source_work)
    (source_work / "README.md").write_text("main\n", encoding="utf-8")
    run_git("add", "README.md", cwd=source_work)
    run_git("commit", "-m", "Create main", cwd=source_work)
    run_git("remote", "add", "origin", str(source_bare), cwd=source_work)
    run_git("push", "origin", "main", cwd=source_work)

    run_git("switch", "-c", "pull-request", cwd=source_work)
    (source_work / "pull-request.txt").write_text("preserve me\n", encoding="utf-8")
    run_git("add", "pull-request.txt", cwd=source_work)
    run_git("commit", "-m", "Pull request only commit", cwd=source_work)
    pull_commit = run_git("rev-parse", "HEAD", cwd=source_work)
    run_git("push", "origin", "HEAD:refs/pull/10/head", cwd=source_work)

    mirror = GitMirror(
        timeout_seconds=60,
        lfs_enabled=False,
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

    cached_mirror = mirror._cached_mirror_path("owned-123", "repository.git")
    assert cached_mirror is not None and cached_mirror.is_dir()

    assert (
        run_git("rev-parse", "refs/githarbor/github-pull/10/head", cwd=destination_bare)
        == pull_commit
    )
    result = subprocess.run(
        ["git", "show-ref", "--verify", "refs/pull/10/head"],
        cwd=destination_bare,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0

    run_git("update-ref", "-d", "refs/pull/10/head", cwd=source_bare)
    await mirror.mirror(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="owned-123",
    )

    result = subprocess.run(
        ["git", "show-ref", "--verify", "refs/githarbor/github-pull/10/head"],
        cwd=destination_bare,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0

    shutil.rmtree(cached_mirror)
    cached_mirror.mkdir()
    (cached_mirror / "invalid-cache-entry").write_text("not a Git repository\n")
    await mirror.mirror(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="owned-123",
    )
    assert run_git("rev-parse", "--is-bare-repository", cwd=cached_mirror) == "true"
