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


def ref_exists(repository: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", ref],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.asyncio
async def test_special_refs_are_preserved_outside_giteas_reserved_namespaces(
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
    run_git("push", "origin", "HEAD:refs/for/master", cwd=source_work)

    mirror = GitMirror(
        timeout_seconds=60,
        lfs_enabled=False,
        cache_path=tmp_path / "git-mirror-cache",
        pull_refs_enabled=True,
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
    assert (
        run_git("rev-parse", "refs/githarbor/gerrit-for/master", cwd=destination_bare)
        == pull_commit
    )
    for reserved_ref in ("refs/pull/10/head", "refs/for/master"):
        assert not ref_exists(destination_bare, reserved_ref)

    run_git("update-ref", "-d", "refs/pull/10/head", cwd=source_bare)
    run_git("update-ref", "-d", "refs/for/master", cwd=source_bare)
    await mirror.mirror(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="owned-123",
    )

    for preserved_ref in (
        "refs/githarbor/github-pull/10/head",
        "refs/githarbor/gerrit-for/master",
    ):
        assert not ref_exists(destination_bare, preserved_ref)

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


@pytest.mark.asyncio
async def test_pull_refs_are_not_fetched_or_pushed_by_default(tmp_path: Path) -> None:
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
    main_commit = run_git("rev-parse", "HEAD", cwd=source_work)
    run_git("remote", "add", "origin", str(source_bare), cwd=source_work)
    run_git("push", "origin", "main", cwd=source_work)

    run_git("switch", "-c", "pull-request", cwd=source_work)
    (source_work / "pull-request.txt").write_text("excluded\n", encoding="utf-8")
    run_git("add", "pull-request.txt", cwd=source_work)
    run_git("commit", "-m", "Pull request only commit", cwd=source_work)
    pull_commit = run_git("rev-parse", "HEAD", cwd=source_work)
    run_git("push", "origin", "HEAD:refs/pull/123/head", cwd=source_work)

    mirror = GitMirror(
        timeout_seconds=60,
        lfs_enabled=False,
        cache_path=tmp_path / "git-mirror-cache",
    )
    await mirror.mirror(
        source_url=source_bare.as_uri(),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="starred-12888993",
    )

    cached_mirror = mirror._cached_mirror_path("starred-12888993", "repository.git")
    assert cached_mirror is not None
    assert run_git("rev-parse", "refs/heads/main", cwd=destination_bare) == main_commit
    assert not ref_exists(destination_bare, "refs/pull/123/head")
    assert not ref_exists(destination_bare, "refs/githarbor/github-pull/123/head")
    assert not ref_exists(cached_mirror, "refs/pull/123/head")
    assert not ref_exists(cached_mirror, "refs/githarbor/github-pull/123/head")
    fetch_refspecs = run_git(
        "config", "--get-all", "remote.origin.fetch", cwd=cached_mirror
    ).splitlines()
    assert fetch_refspecs == [
        "+refs/*:refs/*",
        "^refs/pull/*",
    ]
    missing_pull_commit = subprocess.run(
        ["git", "cat-file", "-e", f"{pull_commit}^{{commit}}"],
        cwd=cached_mirror,
        capture_output=True,
        check=False,
    )
    assert missing_pull_commit.returncode != 0

    run_git("config", "--unset-all", "remote.origin.fetch", cwd=cached_mirror)
    run_git("config", "--add", "remote.origin.fetch", "+refs/*:refs/*", cwd=cached_mirror)
    run_git("update-ref", "refs/pull/legacy/head", main_commit, cwd=cached_mirror)
    await mirror.mirror(
        source_url=source_bare.as_uri(),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
        cache_key="starred-12888993",
    )
    assert not ref_exists(cached_mirror, "refs/pull/legacy/head")
    fetch_refspecs = run_git(
        "config", "--get-all", "remote.origin.fetch", cwd=cached_mirror
    ).splitlines()
    assert fetch_refspecs == [
        "+refs/*:refs/*",
        "^refs/pull/*",
    ]
