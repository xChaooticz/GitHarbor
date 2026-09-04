from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from githarbor.services.redaction import redact


class GitError(RuntimeError):
    pass


class GitMirror:
    destination_remote = "githarbor-destination"
    github_pull_prefix = "refs/pull/"
    preserved_pull_prefix = "refs/githarbor/github-pull/"

    def __init__(self, timeout_seconds: int = 3600, lfs_enabled: bool = True) -> None:
        self.timeout_seconds = timeout_seconds
        self.lfs_enabled = lfs_enabled

    async def mirror(
        self,
        source_url: str,
        source_token: str,
        destination_url: str,
        destination_token: str,
        destination_username: str,
    ) -> None:
        await self._mirror_repository(
            source_url=source_url,
            source_token=source_token,
            destination_url=destination_url,
            destination_token=destination_token,
            destination_username=destination_username,
            transfer_lfs=self.lfs_enabled,
            directory_name="repository.git",
        )

    async def mirror_wiki(
        self,
        source_url: str,
        source_token: str,
        destination_url: str,
        destination_token: str,
        destination_username: str,
    ) -> None:
        await self._mirror_repository(
            source_url=source_url,
            source_token=source_token,
            destination_url=destination_url,
            destination_token=destination_token,
            destination_username=destination_username,
            transfer_lfs=False,
            directory_name="wiki.git",
        )

    async def remote_has_refs(self, source_url: str, source_token: str) -> bool:
        with tempfile.TemporaryDirectory(prefix="githarbor-wiki-check-") as temporary:
            root = Path(temporary)
            askpass = self._create_askpass(root)
            returncode, stdout, stderr = await self._execute(
                ["git", "ls-remote", "--heads", "--tags", "--", source_url],
                root,
                self._environment(askpass, "x-access-token", source_token),
            )
        if returncode == 0:
            return bool(stdout.strip())
        detail = stderr.decode("utf-8", errors="replace").casefold()
        if "repository not found" in detail or "does not appear to be a git repository" in detail:
            return False
        raise GitError(redact(detail.strip() or "Git remote check failed", [source_token])[:4000])

    async def _mirror_repository(
        self,
        source_url: str,
        source_token: str,
        destination_url: str,
        destination_token: str,
        destination_username: str,
        *,
        transfer_lfs: bool,
        directory_name: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="githarbor-") as temporary:
            root = Path(temporary)
            askpass = self._create_askpass(root)
            mirror_path = root / directory_name
            await self._run(
                self.clone_command(source_url, mirror_path),
                root,
                self._environment(askpass, "x-access-token", source_token),
                [source_token],
            )
            if transfer_lfs:
                await self._run(
                    self.lfs_fetch_command(mirror_path, source_url),
                    root,
                    self._environment(askpass, "x-access-token", source_token),
                    [source_token],
                )
                await self._run(
                    self.add_remote_command(mirror_path, destination_url),
                    root,
                    self._environment(askpass, destination_username, destination_token),
                    [destination_token],
                )
                await self._run(
                    self.lfs_push_command(mirror_path, destination_url),
                    root,
                    self._environment(askpass, destination_username, destination_token),
                    [destination_token],
                )
            await self._remap_github_pull_refs(
                mirror_path,
                root,
                self._environment(askpass, "x-access-token", source_token),
                [source_token],
            )
            await self._run(
                self.push_command(mirror_path, destination_url),
                root,
                self._environment(askpass, destination_username, destination_token),
                [destination_token],
            )

    @staticmethod
    def clone_command(source_url: str, mirror_path: Path) -> list[str]:
        return ["git", "clone", "--mirror", "--", source_url, str(mirror_path)]

    @staticmethod
    def push_command(mirror_path: Path, destination_url: str) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            "push",
            "--no-verify",
            "--mirror",
            "--",
            destination_url,
        ]

    @staticmethod
    def list_special_refs_command(mirror_path: Path) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/pull/",
            "refs/githarbor/github-pull/",
        ]

    @staticmethod
    def update_refs_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "update-ref", "--stdin"]

    async def _remap_github_pull_refs(
        self,
        mirror_path: Path,
        cwd: Path,
        env: Mapping[str, str],
        secrets: list[str],
    ) -> None:
        returncode, stdout, stderr = await self._execute(
            self.list_special_refs_command(mirror_path), cwd, env
        )
        if returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GitError(redact(detail or "Unable to inspect Git refs", secrets)[:4000])

        refs: dict[str, str] = {}
        for line in stdout.decode("utf-8", errors="strict").splitlines():
            ref, object_id = line.split(" ", 1)
            refs[ref] = object_id

        transaction = ["start"]
        for ref, object_id in refs.items():
            if not ref.startswith(self.github_pull_prefix):
                continue
            suffix = ref.removeprefix(self.github_pull_prefix)
            preserved_ref = f"{self.preserved_pull_prefix}{suffix}"
            preserved_object_id = refs.get(preserved_ref)
            if preserved_object_id is not None and preserved_object_id != object_id:
                raise GitError(
                    f"Source ref {ref} collides with reserved preservation ref {preserved_ref}"
                )
            if preserved_object_id is None:
                transaction.append(f"create {preserved_ref} {object_id}")
            transaction.append(f"delete {ref} {object_id}")
        if len(transaction) == 1:
            return
        transaction.extend(("prepare", "commit", ""))
        await self._run(
            self.update_refs_command(mirror_path),
            cwd,
            env,
            secrets,
            "\n".join(transaction).encode("utf-8"),
        )

    @classmethod
    def add_remote_command(cls, mirror_path: Path, destination_url: str) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            "remote",
            "add",
            cls.destination_remote,
            destination_url,
        ]

    @staticmethod
    def lfs_fetch_command(mirror_path: Path, source_url: str) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            *GitMirror._lfs_url_arguments(source_url, "origin"),
            "lfs",
            "fetch",
            "--all",
            "origin",
        ]

    @classmethod
    def lfs_push_command(cls, mirror_path: Path, destination_url: str) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            *cls._lfs_url_arguments(destination_url, cls.destination_remote, push=True),
            "lfs",
            "push",
            "--all",
            cls.destination_remote,
        ]

    @staticmethod
    def _lfs_url_arguments(remote_url: str, remote_name: str, *, push: bool = False) -> list[str]:
        parsed = urlsplit(remote_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ["-c", "lfs.allowincompletepush=false"] if push else []
        path = f"{parsed.path.rstrip('/')}/info/lfs"
        lfs_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        settings = [
            f"lfs.url={lfs_url}",
            f"remote.{remote_name}.lfsurl={lfs_url}",
        ]
        if push:
            settings.extend(
                (
                    f"lfs.pushurl={lfs_url}",
                    f"remote.{remote_name}.lfspushurl={lfs_url}",
                    "lfs.allowincompletepush=false",
                )
            )
        return [part for setting in settings for part in ("-c", setting)]

    @staticmethod
    def _create_askpass(root: Path) -> Path:
        path = root / "askpass.sh"
        path.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *Username*) printf '%s\\n' \"$GITHARBOR_GIT_USERNAME\" ;;\n"
            "  *) printf '%s\\n' \"$GITHARBOR_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    @staticmethod
    def _environment(askpass: Path, username: str, token: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_ASKPASS": str(askpass),
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GITHARBOR_GIT_USERNAME": username,
                "GITHARBOR_GIT_TOKEN": token,
                "LC_ALL": "C.UTF-8",
            }
        )
        return env

    async def _run(
        self,
        command: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        secrets: list[str],
        input_data: bytes | None = None,
    ) -> None:
        returncode, stdout, stderr = await self._execute(command, cwd, env, input_data)
        if returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise GitError(redact(detail or "Git command failed", secrets)[:4000])

    async def _execute(
        self,
        command: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        input_data: bytes | None = None,
    ) -> tuple[int, bytes, bytes]:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdin=(
                    asyncio.subprocess.PIPE
                    if input_data is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            if process is not None:
                process.kill()
                await process.communicate()
            raise GitError(f"Git operation timed out after {self.timeout_seconds} seconds") from exc
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
            raise
        except OSError as exc:
            raise GitError(f"Unable to execute Git: {exc.__class__.__name__}") from exc
        assert process.returncode is not None
        return process.returncode, stdout, stderr
