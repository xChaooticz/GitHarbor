from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from githarbor.services.redaction import redact

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    pass


class GitMirror:
    destination_remote = "githarbor-destination"
    github_pull_prefix = "refs/pull/"
    preserved_pull_prefix = "refs/githarbor/github-pull/"

    def __init__(
        self,
        timeout_seconds: int = 3600,
        lfs_enabled: bool = True,
        cache_path: Path | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.lfs_enabled = lfs_enabled
        self.cache_path = cache_path

    async def mirror(
        self,
        source_url: str,
        source_token: str,
        destination_url: str,
        destination_token: str,
        destination_username: str,
        cache_key: str | None = None,
        source_username: str = "x-access-token",
    ) -> None:
        await self._mirror_repository(
            source_url=source_url,
            source_token=source_token,
            destination_url=destination_url,
            destination_token=destination_token,
            destination_username=destination_username,
            transfer_lfs=self.lfs_enabled,
            directory_name="repository.git",
            preserve_destination_head=False,
            cache_key=cache_key,
            source_username=source_username,
        )

    async def mirror_wiki(
        self,
        source_url: str,
        source_token: str,
        destination_url: str,
        destination_token: str,
        destination_username: str,
        cache_key: str | None = None,
        source_username: str = "x-access-token",
    ) -> None:
        await self._mirror_repository(
            source_url=source_url,
            source_token=source_token,
            destination_url=destination_url,
            destination_token=destination_token,
            destination_username=destination_username,
            transfer_lfs=False,
            directory_name="wiki.git",
            preserve_destination_head=True,
            cache_key=cache_key,
            source_username=source_username,
        )

    async def remote_has_refs(
        self, source_url: str, source_token: str, source_username: str = "x-access-token"
    ) -> bool:
        with tempfile.TemporaryDirectory(prefix="githarbor-wiki-check-") as temporary:
            root = Path(temporary)
            askpass = self._create_askpass(root)
            returncode, stdout, stderr = await self._execute(
                ["git", "ls-remote", "--heads", "--tags", "--", source_url],
                root,
                self._environment(askpass, source_username, source_token),
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
        preserve_destination_head: bool,
        cache_key: str | None,
        source_username: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="githarbor-") as temporary:
            root = Path(temporary)
            askpass = self._create_askpass(root)
            mirror_path = self._cached_mirror_path(cache_key, directory_name)
            if mirror_path is None:
                mirror_path = root / directory_name
                await self._clone(
                    source_url, mirror_path, root, askpass, source_username, source_token
                )
            elif mirror_path.is_dir() and not mirror_path.is_symlink():
                await self._refresh_cached_mirror(
                    source_url, mirror_path, root, askpass, source_username, source_token
                )
            else:
                await self._replace_cached_mirror(
                    source_url, mirror_path, root, askpass, source_username, source_token
                )
            if transfer_lfs:
                await self._run(
                    self.lfs_fetch_command(mirror_path, source_url),
                    root,
                    self._environment(askpass, source_username, source_token),
                    [source_token],
                )
                await self._run(
                    self.configure_destination_remote_command(mirror_path, destination_url),
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
                self._environment(askpass, source_username, source_token),
                [source_token],
            )
            push_command = self.push_command(mirror_path, destination_url)
            if preserve_destination_head:
                source_head = await self._local_head_ref(
                    mirror_path, root, self._environment(askpass, source_username, source_token)
                )
                destination_head = await self._destination_head_ref(
                    destination_url,
                    root,
                    self._environment(askpass, destination_username, destination_token),
                    [destination_token],
                )
                push_command = self.wiki_push_command(
                    mirror_path, destination_url, source_head, destination_head
                )
            await self._run(
                push_command,
                root,
                self._environment(askpass, destination_username, destination_token),
                [destination_token],
                retry_transient=True,
            )
            if self.cache_path is not None and cache_key is not None:
                try:
                    os.utime(mirror_path)
                except OSError as exc:
                    logger.warning(
                        "Unable to update Git mirror cache timestamp for %s: %s",
                        mirror_path.name,
                        exc.__class__.__name__,
                    )

    def _cached_mirror_path(self, cache_key: str | None, directory_name: str) -> Path | None:
        if self.cache_path is None or cache_key is None:
            return None
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_path / f"{digest}-{directory_name}"

    async def _clone(
        self,
        source_url: str,
        mirror_path: Path,
        root: Path,
        askpass: Path,
        source_username: str,
        source_token: str,
    ) -> None:
        await self._run(
            self.clone_command(source_url, mirror_path),
            root,
            self._environment(askpass, source_username, source_token),
            [source_token],
        )

    async def _refresh_cached_mirror(
        self,
        source_url: str,
        mirror_path: Path,
        root: Path,
        askpass: Path,
        source_username: str,
        source_token: str,
    ) -> None:
        environment = self._environment(askpass, source_username, source_token)
        if not await self._is_bare_mirror(mirror_path, root, environment):
            logger.warning("Rebuilding invalid Git mirror cache entry: %s", mirror_path.name)
            await self._replace_cached_mirror(
                source_url, mirror_path, root, askpass, source_username, source_token
            )
            return
        try:
            await self._run(
                self.set_remote_url_command(mirror_path, source_url),
                root,
                environment,
                [source_token],
            )
        except GitError:
            logger.warning("Rebuilding misconfigured Git mirror cache entry: %s", mirror_path.name)
            await self._replace_cached_mirror(
                source_url, mirror_path, root, askpass, source_username, source_token
            )
            return
        try:
            await self._run(
                self.fetch_command(mirror_path),
                root,
                environment,
                [source_token],
            )
        except GitError:
            if await self._is_healthy_mirror(mirror_path, root, environment):
                raise
            logger.warning("Rebuilding corrupt Git mirror cache entry: %s", mirror_path.name)
            await self._replace_cached_mirror(
                source_url, mirror_path, root, askpass, source_username, source_token
            )

    async def _is_bare_mirror(
        self, mirror_path: Path, root: Path, environment: Mapping[str, str]
    ) -> bool:
        returncode, stdout, _stderr = await self._execute(
            self.bare_mirror_check_command(mirror_path), root, environment
        )
        if returncode != 0 or stdout.strip() != b"true":
            return False
        returncode, stdout, _stderr = await self._execute(
            self.source_refspec_check_command(mirror_path), root, environment
        )
        return returncode == 0 and stdout.splitlines() == [b"+refs/*:refs/*"]

    async def _is_healthy_mirror(
        self, mirror_path: Path, root: Path, environment: Mapping[str, str]
    ) -> bool:
        returncode, _stdout, _stderr = await self._execute(
            self.mirror_health_check_command(mirror_path), root, environment
        )
        return returncode == 0

    async def _replace_cached_mirror(
        self,
        source_url: str,
        mirror_path: Path,
        root: Path,
        askpass: Path,
        source_username: str,
        source_token: str,
    ) -> None:
        mirror_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".githarbor-rebuild-", dir=mirror_path.parent
        ) as staging:
            staged_mirror = Path(staging) / mirror_path.name
            await self._clone(
                source_url, staged_mirror, root, askpass, source_username, source_token
            )
            replaced_path: Path | None = None
            if mirror_path.exists() or mirror_path.is_symlink():
                replaced_path = mirror_path.with_name(f".githarbor-replaced-{uuid.uuid4().hex}")
                os.replace(mirror_path, replaced_path)
            try:
                os.replace(staged_mirror, mirror_path)
            except Exception:
                if replaced_path is not None and not mirror_path.exists():
                    os.replace(replaced_path, mirror_path)
                raise
            if replaced_path is not None:
                try:
                    self._remove_cache_entry(replaced_path)
                except OSError as exc:
                    logger.warning(
                        "Unable to remove replaced Git mirror cache entry %s: %s",
                        replaced_path.name,
                        exc.__class__.__name__,
                    )

    @staticmethod
    def _remove_cache_entry(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    async def maintain_cache(
        self,
        active_entries: set[tuple[str, str]],
        retention_days: int,
    ) -> None:
        if self.cache_path is None or not self.cache_path.is_dir():
            return
        active_names = {
            path.name
            for cache_key, directory_name in active_entries
            if (path := self._cached_mirror_path(cache_key, directory_name)) is not None
        }
        now = time.time()
        retention_seconds = retention_days * 24 * 60 * 60
        temporary_retention_seconds = 24 * 60 * 60
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C.UTF-8",
            }
        )
        removed = 0
        for entry in self.cache_path.iterdir():
            try:
                age_seconds = max(0.0, now - entry.stat(follow_symlinks=False).st_mtime)
                if entry.name in active_names:
                    if entry.is_dir() and not entry.is_symlink():
                        returncode, _stdout, stderr = await self._execute(
                            self.cache_gc_command(entry), self.cache_path, environment
                        )
                        if returncode:
                            detail = stderr.decode("utf-8", errors="replace").strip()
                            logger.warning(
                                "Git mirror cache maintenance failed for %s: %s",
                                entry.name,
                                redact(detail or "git gc failed")[:1000],
                            )
                    continue
                if entry.name.startswith((".githarbor-rebuild-", ".githarbor-replaced-")):
                    if age_seconds >= temporary_retention_seconds:
                        self._remove_cache_entry(entry)
                        removed += 1
                    continue
                if self._is_managed_cache_name(entry.name) and age_seconds >= retention_seconds:
                    self._remove_cache_entry(entry)
                    removed += 1
            except OSError as exc:
                logger.warning(
                    "Unable to maintain Git mirror cache entry %s: %s",
                    entry.name,
                    exc.__class__.__name__,
                )
        if removed:
            logger.info("Removed %d expired Git mirror cache entries", removed)

    @staticmethod
    def _is_managed_cache_name(name: str) -> bool:
        suffixes = ("-repository.git", "-wiki.git")
        suffix = next((candidate for candidate in suffixes if name.endswith(candidate)), None)
        if suffix is None:
            return False
        digest = name.removesuffix(suffix)
        return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)

    @staticmethod
    def clone_command(source_url: str, mirror_path: Path) -> list[str]:
        return ["git", "clone", "--mirror", "--", source_url, str(mirror_path)]

    @staticmethod
    def set_remote_url_command(mirror_path: Path, source_url: str) -> list[str]:
        return ["git", "-C", str(mirror_path), "remote", "set-url", "origin", source_url]

    @staticmethod
    def fetch_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "fetch", "--prune", "origin"]

    @staticmethod
    def bare_mirror_check_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "rev-parse", "--is-bare-repository"]

    @staticmethod
    def source_refspec_check_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "config", "--get-all", "remote.origin.fetch"]

    @staticmethod
    def mirror_health_check_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "fsck", "--connectivity-only", "--no-dangling"]

    @staticmethod
    def cache_gc_command(mirror_path: Path) -> list[str]:
        return ["git", "-C", str(mirror_path), "gc", "--auto"]

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
    def wiki_push_command(
        mirror_path: Path, destination_url: str, source_head: str, destination_head: str
    ) -> list[str]:
        refspecs = ["refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"]
        if source_head != destination_head:
            refspecs.append(f"{source_head}:{destination_head}")
        return [
            "git",
            "-C",
            str(mirror_path),
            "push",
            "--no-verify",
            "--force",
            "--",
            destination_url,
            *refspecs,
        ]

    async def _local_head_ref(self, mirror_path: Path, cwd: Path, env: Mapping[str, str]) -> str:
        returncode, stdout, stderr = await self._execute(
            ["git", "-C", str(mirror_path), "symbolic-ref", "HEAD"], cwd, env
        )
        if returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GitError(redact(detail or "Unable to determine source wiki branch", [])[:4000])
        return self._parse_head_ref(stdout, "source wiki")

    async def _destination_head_ref(
        self,
        destination_url: str,
        cwd: Path,
        env: Mapping[str, str],
        secrets: list[str],
    ) -> str:
        returncode, stdout, stderr = await self._execute(
            ["git", "ls-remote", "--symref", "--", destination_url, "HEAD"], cwd, env
        )
        if returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GitError(
                redact(detail or "Unable to determine destination wiki branch", secrets)[:4000]
            )
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            prefix, separator, name = line.partition("\t")
            if separator and name == "HEAD" and prefix.startswith("ref: "):
                return self._parse_head_ref(
                    prefix.removeprefix("ref: ").encode(), "destination wiki"
                )
        raise GitError("Destination wiki did not advertise its default branch")

    @staticmethod
    def _parse_head_ref(value: bytes, description: str) -> str:
        ref = value.decode("utf-8", errors="replace").strip()
        if not ref.startswith("refs/heads/"):
            raise GitError(f"Invalid {description} default branch: {ref or 'missing'}")
        return ref

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
    def configure_destination_remote_command(
        cls, mirror_path: Path, destination_url: str
    ) -> list[str]:
        return [
            "git",
            "-C",
            str(mirror_path),
            "config",
            f"remote.{cls.destination_remote}.url",
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
        retry_transient: bool = False,
    ) -> None:
        for attempt in range(3):
            returncode, stdout, stderr = await self._execute(command, cwd, env, input_data)
            if not returncode:
                return
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            message = redact(detail or "Git command failed", secrets)[:4000]
            if not retry_transient or attempt == 2 or not self._is_transient_push_failure(message):
                raise GitError(message)
            await asyncio.sleep(2**attempt)

    @staticmethod
    def _is_transient_push_failure(message: str) -> bool:
        normalized = message.casefold()
        return any(
            pattern in normalized
            for pattern in (
                "http 502",
                "http 503",
                "http 504",
                "unexpected disconnect",
                "remote end hung up unexpectedly",
                "connection reset by peer",
            )
        )

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
