from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from githarbor.clients.gitea import GiteaClient
from githarbor.clients.github import GitHubClient, UpstreamRepository
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.models import (
    Repository,
    RepositoryKind,
    RepositoryStatus,
    RunStatus,
    SyncRun,
    utcnow,
)
from githarbor.services.containers import ContainerMirrorService
from githarbor.services.git import GitMirror
from githarbor.services.naming import collision_destination_name, destination_name
from githarbor.services.reconciliation import Reconciler
from githarbor.services.redaction import redact
from githarbor.services.releases import ReleaseMirrorService

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        github: GitHubClient,
        gitea: GiteaClient,
        git: GitMirror,
        container_mirror: ContainerMirrorService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.github = github
        self.gitea = gitea
        self.git = git
        self.container_mirror = container_mirror
        self.release_mirror = ReleaseMirrorService(github, gitea)
        self.reconciler = Reconciler()
        self.global_lock = asyncio.Lock()
        self._repository_locks: dict[int, asyncio.Lock] = {}
        self._global_task: asyncio.Task[None] | None = None
        self._repository_tasks: dict[int, asyncio.Task[bool]] = {}
        self.github_status = "unknown"
        self.gitea_status = "unknown"
        self.recover_interrupted_runs()

    @property
    def is_running(self) -> bool:
        return (
            self.global_lock.locked()
            or (self._global_task is not None and not self._global_task.done())
            or any(not task.done() for task in self._repository_tasks.values())
        )

    def start_global_sync(self, trigger: str) -> bool:
        if self.is_running:
            return False
        self._global_task = asyncio.create_task(self.sync_all(trigger))
        return True

    def start_repository_sync(self, repository_id: int, trigger: str) -> bool:
        task = self._repository_tasks.get(repository_id)
        if task is not None and not task.done():
            return False
        self._repository_tasks[repository_id] = asyncio.create_task(
            self.sync_repository(repository_id, trigger=trigger)
        )
        return True

    async def shutdown(self) -> None:
        tasks = [
            task
            for task in [self._global_task, *self._repository_tasks.values()]
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_active_syncs(self) -> int:
        """Cancel current work without stopping the service or its scheduler."""
        tasks = [
            task
            for task in [self._global_task, *self._repository_tasks.values()]
            if task is not None and not task.done()
        ]
        if not tasks:
            return 0
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        message = "Synchronization stopped by an administrator"
        now = utcnow()
        with self.database.session_factory.begin() as session:
            session.execute(
                update(Repository)
                .where(Repository.status == RepositoryStatus.SYNCING.value)
                .values(
                    status=RepositoryStatus.ERROR.value,
                    last_error=message,
                    last_warning=None,
                    updated_at=now,
                )
            )
            session.execute(
                update(SyncRun)
                .where(SyncRun.status == RunStatus.RUNNING.value)
                .values(status=RunStatus.SKIPPED.value, message=message, finished_at=now)
            )
        logger.warning("Stopped %d active synchronization task(s)", len(tasks))
        return len(tasks)

    def recover_interrupted_runs(self) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            session.execute(
                update(Repository)
                .where(Repository.status == RepositoryStatus.SYNCING.value)
                .values(
                    status=RepositoryStatus.ERROR.value,
                    last_error="Synchronization interrupted by application restart",
                    updated_at=now,
                )
            )
            session.execute(
                update(SyncRun)
                .where(SyncRun.status == RunStatus.RUNNING.value)
                .values(
                    status=RunStatus.FAILED.value,
                    message="Synchronization interrupted by application restart",
                    finished_at=now,
                )
            )

    async def sync_all(self, trigger: str = "manual") -> None:
        if self.global_lock.locked():
            return
        async with self.global_lock:
            run_id = self._start_run(None, "global", trigger)
            discovered_by_id: dict[tuple[int, str], UpstreamRepository] = {}
            discovery_errors: list[str] = []
            counts = {RepositoryKind.OWNED: 0, RepositoryKind.STARRED: 0}
            for kind, discover, namespace in (
                (RepositoryKind.OWNED, self.github.list_owned, self.settings.gitea_owned_namespace),
                (
                    RepositoryKind.STARRED,
                    self.github.list_starred,
                    self.settings.gitea_starred_namespace,
                ),
            ):
                try:
                    repositories = await discover()
                    self.github_status = "connected"
                    counts[kind] = len(repositories)
                    seen_at = datetime.now(UTC)
                    with self.database.session_factory() as session:
                        ids = self.reconciler.reconcile(
                            session, repositories, kind, namespace, seen_at
                        )
                    upstream_by_github_id = {item.github_id: item for item in repositories}
                    with self.database.session_factory() as session:
                        for local_id in ids:
                            local = session.get(Repository, local_id)
                            if local is not None:
                                discovered_by_id[(local_id, kind.value)] = upstream_by_github_id[
                                    local.github_id
                                ]
                except Exception as exc:
                    self.github_status = "error"
                    message = redact(str(exc))
                    discovery_errors.append(f"{kind.value}: {message}")
                    logger.error("%s repository discovery failed: %s", kind.value, message)

            succeeded = 0
            failed = 0
            warnings = 0
            for (repository_id, _kind), upstream in discovered_by_id.items():
                if await self.sync_repository(repository_id, trigger="global", upstream=upstream):
                    succeeded += 1
                    with self.database.session_factory() as session:
                        repository = session.get(Repository, repository_id)
                        if repository is not None and repository.last_warning:
                            warnings += 1
                else:
                    failed += 1

            if discovery_errors and not discovered_by_id:
                status = RunStatus.FAILED
            elif discovery_errors or failed or warnings:
                status = RunStatus.PARTIAL
            else:
                status = RunStatus.SUCCESS
            self._finish_run(
                run_id,
                status,
                "; ".join(
                    [
                        *discovery_errors,
                        *([f"{warnings} repositories had warnings"] if warnings else []),
                    ]
                )
                or None,
                discovered_owned=counts[RepositoryKind.OWNED],
                discovered_starred=counts[RepositoryKind.STARRED],
                succeeded=succeeded,
                failed=failed,
            )
            logger.info(
                "Global synchronization finished: status=%s succeeded=%d failed=%d",
                status.value,
                succeeded,
                failed,
            )

    async def sync_repository(
        self,
        repository_id: int,
        trigger: str = "manual",
        upstream: UpstreamRepository | None = None,
    ) -> bool:
        lock = self._repository_locks.setdefault(repository_id, asyncio.Lock())
        if lock.locked():
            return False
        async with lock:
            with self.database.session_factory() as session:
                repository = session.get(Repository, repository_id)
                if repository is None:
                    return False
                previous_status = repository.status
                repository.status = RepositoryStatus.SYNCING.value
                repository.last_sync_attempt_at = utcnow()
                repository.last_error = None
                repository.last_warning = None
                session.commit()
                full_name = repository.upstream_full_name
                github_id = repository.github_id
                kind = repository.kind
                namespace = repository.destination_namespace
                destination = repository.destination_name
            run_id = self._start_run(repository_id, "repository", trigger)
            try:
                if upstream is None:
                    upstream = await self.github.get_repository(full_name)
                    if upstream.github_id != github_id:
                        raise RuntimeError(
                            "Upstream path now refers to a different GitHub repository ID; "
                            "refusing sync"
                        )
                requested_destination = destination
                fallback_destination: str | None = None
                if kind == RepositoryKind.STARRED.value:
                    preferred = destination_name(
                        upstream.owner, upstream.name, upstream.github_id, kind
                    )
                    collision_safe = collision_destination_name(
                        upstream.owner, upstream.name, upstream.github_id, kind
                    )
                    if destination == preferred:
                        fallback_destination = collision_safe
                    elif destination == collision_safe:
                        requested_destination = preferred
                        fallback_destination = collision_safe

                destination_repo = await self.gitea.ensure_repository(
                    namespace=namespace,
                    name=requested_destination,
                    github_id=github_id,
                    kind=kind,
                    source_full_name=upstream.full_name,
                    source_description=upstream.description,
                    private=self.settings.destination_private,
                    fallback_name=fallback_destination,
                )
                destination = destination_repo.name
                with self.database.session_factory.begin() as session:
                    repository = session.get(Repository, repository_id)
                    assert repository is not None
                    repository.destination_name = destination
                    repository.destination_url = destination_repo.html_url
                user = await self.gitea.authenticated_user()
                self.gitea_status = "connected"
                secrets = [
                    self.settings.github_token.get_secret_value(),
                    self.settings.gitea_token.get_secret_value(),
                ]
                await self.git.mirror(
                    source_url=upstream.clone_url,
                    source_token=self.settings.github_token.get_secret_value(),
                    destination_url=destination_repo.clone_url,
                    destination_token=self.settings.gitea_token.get_secret_value(),
                    destination_username=str(user["login"]),
                )
                if upstream.default_branch:
                    await self.gitea.set_default_branch(
                        namespace, destination, upstream.default_branch
                    )
                optional_warnings: list[str] = []
                if self.settings.wiki_enabled and upstream.has_wiki:
                    try:
                        has_wiki_content = await self.git.remote_has_refs(
                            upstream.wiki_clone_url,
                            self.settings.github_token.get_secret_value(),
                        )
                        if has_wiki_content:
                            await self.gitea.enable_wiki(namespace, destination)
                            await self.git.mirror_wiki(
                                source_url=upstream.wiki_clone_url,
                                source_token=self.settings.github_token.get_secret_value(),
                                destination_url=destination_repo.wiki_clone_url,
                                destination_token=self.settings.gitea_token.get_secret_value(),
                                destination_username=str(user["login"]),
                            )
                            logger.info(
                                "Mirrored wiki for GitHub repository %s", upstream.full_name
                            )
                        else:
                            logger.info(
                                "Skipped enabled but empty wiki for GitHub repository %s",
                                upstream.full_name,
                            )
                    except Exception as exc:
                        message = redact(str(exc), secrets)[:1000]
                        optional_warnings.append(f"wiki mirror failed: {message}")
                        logger.warning(
                            "Wiki mirror for GitHub repository %s failed after the primary "
                            "repository was preserved: %s",
                            upstream.full_name,
                            message,
                        )
                release_warnings: list[str] = []
                if self.settings.releases_enabled:
                    try:
                        release_warnings = await self.release_mirror.mirror(
                            upstream.full_name,
                            namespace,
                            destination,
                            mirror_assets=self.settings.release_assets_enabled,
                            asset_mode=self.settings.release_asset_mode,
                        )
                    except Exception as exc:
                        message = redact(str(exc), secrets)[:1000]
                        optional_warnings.append(f"release mirror failed: {message}")
                        logger.warning(
                            "Release mirror for GitHub repository %s failed after the primary "
                            "repository was preserved: %s",
                            upstream.full_name,
                            message,
                        )
                package_warnings: list[str] = []
                if self.settings.packages_enabled and kind == RepositoryKind.OWNED.value:
                    if self.container_mirror is None:
                        raise RuntimeError("Container package mirror was not initialized")
                    package_warnings = await self.container_mirror.mirror(
                        repository_id,
                        github_id,
                        namespace,
                        destination,
                        str(user["login"]),
                    )
                warning_message = self._warning_message(
                    [*optional_warnings, *release_warnings, *package_warnings]
                )
                now = utcnow()
                with self.database.session_factory.begin() as session:
                    repository = session.get(Repository, repository_id)
                    assert repository is not None
                    repository.destination_url = destination_repo.html_url
                    repository.last_successful_sync_at = now
                    repository.last_error = None
                    repository.last_warning = warning_message
                    repository.status = (
                        previous_status
                        if previous_status
                        in {
                            RepositoryStatus.UNSTARRED.value,
                            RepositoryStatus.ARCHIVED.value,
                        }
                        and trigger != "global"
                        else RepositoryStatus.ACTIVE.value
                    )
                self._finish_run(
                    run_id,
                    RunStatus.PARTIAL if warning_message else RunStatus.SUCCESS,
                    warning_message,
                    succeeded=1,
                )
                if warning_message:
                    logger.warning(
                        "Repository %d synchronization completed with warnings: %s",
                        repository_id,
                        warning_message,
                    )
                return True
            except Exception as exc:
                secrets = [
                    self.settings.github_token.get_secret_value(),
                    self.settings.gitea_token.get_secret_value(),
                ]
                message = redact(
                    str(exc),
                    secrets,
                )[:4000]
                if exc.__class__.__module__.endswith("gitea"):
                    self.gitea_status = "error"
                with self.database.session_factory.begin() as session:
                    repository = session.get(Repository, repository_id)
                    if repository is not None:
                        repository.status = RepositoryStatus.ERROR.value
                        repository.last_error = message
                self._finish_run(run_id, RunStatus.FAILED, message, failed=1)
                logger.error("Repository %d synchronization failed: %s", repository_id, message)
                return False

    @staticmethod
    def _warning_message(warnings: list[str]) -> str | None:
        if not warnings:
            return None
        message = "; ".join(warnings)
        if len(message) <= 4000:
            return message
        return f"{message[:3950].rstrip()}; warning list truncated"

    def status(self, next_sync: datetime | None = None) -> dict[str, Any]:
        with self.database.session_factory() as session:
            total_by_kind: dict[str, int] = dict(
                session.execute(select(Repository.kind, func.count()).group_by(Repository.kind))
                .tuples()
                .all()
            )
            total_by_status: dict[str, int] = dict(
                session.execute(select(Repository.status, func.count()).group_by(Repository.status))
                .tuples()
                .all()
            )
            last_run = session.scalar(
                select(SyncRun)
                .where(SyncRun.scope == "global")
                .order_by(SyncRun.started_at.desc())
                .limit(1)
            )
        return {
            "github": self.github_status,
            "gitea": self.gitea_status,
            "sync_running": self.is_running,
            "last_sync": last_run.as_dict() if last_run else None,
            "next_sync": next_sync,
            "counts": {
                "owned": total_by_kind.get(RepositoryKind.OWNED.value, 0),
                "starred": total_by_kind.get(RepositoryKind.STARRED.value, 0),
                "active": total_by_status.get(RepositoryStatus.ACTIVE.value, 0),
                "unavailable": total_by_status.get(RepositoryStatus.UNAVAILABLE.value, 0),
                "unstarred": total_by_status.get(RepositoryStatus.UNSTARRED.value, 0),
                "error": total_by_status.get(RepositoryStatus.ERROR.value, 0),
            },
        }

    def _start_run(self, repository_id: int | None, scope: str, trigger: str) -> int:
        with self.database.session_factory.begin() as session:
            run = SyncRun(
                repository_id=repository_id,
                scope=scope,
                trigger=trigger,
                status=RunStatus.RUNNING.value,
                started_at=utcnow(),
            )
            session.add(run)
            session.flush()
            return run.id

    def _finish_run(
        self,
        run_id: int,
        status: RunStatus,
        message: str | None,
        **counts: int,
    ) -> None:
        with self.database.session_factory.begin() as session:
            run = session.get(SyncRun, run_id)
            if run is None:
                return
            run.status = status.value
            run.finished_at = utcnow()
            run.message = message
            for key, value in counts.items():
                setattr(run, key, value)
