from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from githarbor.services.git import GitMirror

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def run_git(*arguments: str, cwd: Path | None = None) -> None:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.asyncio
async def test_wiki_mirror_preserves_history_and_detects_empty_source(tmp_path: Path) -> None:
    source_work = tmp_path / "wiki-work"
    source_bare = tmp_path / "source.wiki.git"
    empty_bare = tmp_path / "empty.wiki.git"
    destination_bare = tmp_path / "destination.wiki.git"
    restored = tmp_path / "restored"
    source_work.mkdir()

    run_git("init", "--bare", str(source_bare))
    run_git("init", "--bare", str(empty_bare))
    run_git("init", "--bare", str(destination_bare))
    run_git("init", "--initial-branch=master", cwd=source_work)
    run_git("config", "user.name", "GitHarbor Test", cwd=source_work)
    run_git("config", "user.email", "githarbor@example.test", cwd=source_work)
    (source_work / "Home.md").write_text("# Preserved wiki\n", encoding="utf-8")
    run_git("add", "Home.md", cwd=source_work)
    run_git("commit", "-m", "Create wiki", cwd=source_work)
    (source_work / "Home.md").write_text("# Preserved wiki\n\nSecond revision.\n", encoding="utf-8")
    run_git("commit", "-am", "Expand wiki", cwd=source_work)
    run_git("remote", "add", "origin", str(source_bare), cwd=source_work)
    run_git("push", "origin", "master", cwd=source_work)

    mirror = GitMirror(timeout_seconds=60, lfs_enabled=True)
    assert await mirror.remote_has_refs(str(source_bare), "") is True
    assert await mirror.remote_has_refs(str(empty_bare), "") is False
    await mirror.mirror_wiki(
        source_url=str(source_bare),
        source_token="",
        destination_url=str(destination_bare),
        destination_token="",
        destination_username="",
    )

    run_git("clone", str(destination_bare), str(restored))
    assert (restored / "Home.md").read_text(encoding="utf-8").endswith("Second revision.\n")
    history = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=restored,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert history == ["Expand wiki", "Create wiki"]
