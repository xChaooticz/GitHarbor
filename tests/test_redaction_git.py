from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from githarbor.services.git import GitError, GitMirror
from githarbor.services.redaction import redact


def test_redacts_explicit_secret_and_url_credentials() -> None:
    value = "token=abcd https://user:pass@example.test/repo Authorization: Bearer xyz"
    redacted = redact(value, ["abcd"])
    assert "abcd" not in redacted
    assert "user:pass" not in redacted
    assert "xyz" not in redacted
    assert redacted.count("[REDACTED]") >= 3


def test_git_command_construction_uses_argument_arrays() -> None:
    path = Path("/tmp/repository.git")
    clone = GitMirror.clone_command("https://github.test/a/b.git", path)
    push = GitMirror.push_command(path, "https://gitea.test/archive/a--b.git")
    assert clone == ["git", "clone", "--mirror", "--", "https://github.test/a/b.git", str(path)]
    assert push == [
        "git",
        "-C",
        str(path),
        "push",
        "--no-verify",
        "--mirror",
        "--",
        "https://gitea.test/archive/a--b.git",
    ]
    assert all("secret" not in part for part in clone + push)


def test_lfs_commands_pin_http_endpoints_and_use_a_named_destination_remote() -> None:
    path = Path("/tmp/repository.git")
    source_url = "https://github.test/a/b.git"
    destination_url = "https://gitea.test/archive/a--b.git"

    assert GitMirror.lfs_fetch_command(path, source_url) == [
        "git",
        "-C",
        str(path),
        "-c",
        "lfs.url=https://github.test/a/b.git/info/lfs",
        "-c",
        "remote.origin.lfsurl=https://github.test/a/b.git/info/lfs",
        "lfs",
        "fetch",
        "--all",
        "origin",
    ]
    assert GitMirror.add_remote_command(path, destination_url) == [
        "git",
        "-C",
        str(path),
        "remote",
        "add",
        "githarbor-destination",
        destination_url,
    ]
    assert GitMirror.lfs_push_command(path, destination_url) == [
        "git",
        "-C",
        str(path),
        "-c",
        "lfs.url=https://gitea.test/archive/a--b.git/info/lfs",
        "-c",
        "remote.githarbor-destination.lfsurl=https://gitea.test/archive/a--b.git/info/lfs",
        "-c",
        "lfs.pushurl=https://gitea.test/archive/a--b.git/info/lfs",
        "-c",
        "remote.githarbor-destination.lfspushurl=https://gitea.test/archive/a--b.git/info/lfs",
        "-c",
        "lfs.allowincompletepush=false",
        "lfs",
        "push",
        "--all",
        "githarbor-destination",
    ]


def test_lfs_commands_leave_local_remote_discovery_intact() -> None:
    path = Path("/tmp/repository.git")
    command = GitMirror.lfs_fetch_command(path, "/tmp/source.git")
    assert command == ["git", "-C", str(path), "lfs", "fetch", "--all", "origin"]


@pytest.mark.asyncio
async def test_lfs_upload_failure_prevents_ref_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror = GitMirror(lfs_enabled=True)
    commands: list[Sequence[str]] = []

    async def fail_lfs_upload(command: Sequence[str], *_arguments: object) -> None:
        commands.append(command)
        if "lfs" in command and "push" in command:
            raise GitError("LFS upload failed")

    monkeypatch.setattr(mirror, "_run", fail_lfs_upload)
    with pytest.raises(GitError, match="LFS upload failed"):
        await mirror.mirror(
            "https://github.test/a/b.git",
            "source-secret",
            "https://gitea.test/archive/a--b.git",
            "destination-secret",
            "git-user",
        )

    assert any("lfs" in command and "push" in command for command in commands)
    assert not any("--mirror" in command and "push" in command for command in commands)


@pytest.mark.asyncio
async def test_lfs_can_be_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    mirror = GitMirror(lfs_enabled=False)
    commands: list[Sequence[str]] = []

    async def record(command: Sequence[str], *_arguments: object, **_kwargs: object) -> None:
        commands.append(command)

    async def skip_ref_remap(*_arguments: object) -> None:
        return None

    monkeypatch.setattr(mirror, "_run", record)
    monkeypatch.setattr(mirror, "_remap_github_pull_refs", skip_ref_remap)
    await mirror.mirror(
        "https://github.test/a/b.git",
        "source-secret",
        "https://gitea.test/archive/a--b.git",
        "destination-secret",
        "git-user",
    )

    assert len(commands) == 2
    assert all("lfs" not in command for command in commands)


@pytest.mark.asyncio
async def test_mirror_push_retries_a_transient_gateway_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mirror = GitMirror()
    results = iter(
        [
            (1, b"", b"error: RPC failed; HTTP 504\nfatal: the remote end hung up unexpectedly"),
            (0, b"", b""),
        ]
    )

    async def execute(*_arguments: object) -> tuple[int, bytes, bytes]:
        return next(results)

    sleep = AsyncMock()
    monkeypatch.setattr(mirror, "_execute", execute)
    monkeypatch.setattr("githarbor.services.git.asyncio.sleep", sleep)

    await mirror._run(
        ["git", "push"], tmp_path, {}, [], retry_transient=True
    )

    sleep.assert_awaited_once_with(1)
