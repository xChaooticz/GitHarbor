from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from githarbor.clients.external_releases import ExternalReleaseClient
from githarbor.clients.gitea import GiteaClient
from githarbor.clients.github import GitHubClient, UpstreamRepository
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.external_sources import ExternalRepository, ExternalSources
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

SourceRepository = UpstreamRepository | ExternalRepository


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
        self.external_sources = ExternalSources(settings.external_sources_file)
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
            logger.info("Global synchronization started: trigger=%s", trigger)
            run_id = self._start_run(None, "global", trigger)
            discovered_by_id: dict[tuple[int, str], SourceRepository] = {}
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
                    logger.info("Discovering %s GitHub repositories", kind.value)
                    repositories = await discover()
                    self.github_status = "connected"
                    counts[kind] = len(repositories)
                    logger.info(
                        "Discovered %d %s GitHub repositories", len(repositories), kind.value
                    )
                    seen_at = datetime.now(UTC)
                    with self.database.session_factory() as session:
                        ids = self.reconciler.reconcile(
                            session, repositories, kind, namespace, seen_at
                        )
                    upstream_by_github_id = {item.github_id: item for item in repositories}
                    with self.database.session_factory() as session:
                        for local_id in ids:
                            local = session.get(Repository, local_id)
                            if local is not None and local.github_id is not None:
                                discovered_by_id[(local_id, kind.value)] = upstream_by_github_id[
                                    local.github_id
                                ]
                except Exception as exc:
                    self.github_status = "error"
                    message = redact(str(exc))
                    discovery_errors.append(f"{kind.value}: {message}")
                    logger.error("%s repository discovery failed: %s", kind.value, message)

            if self.external_sources.enabled:
                try:
                    logger.info("Loading external repositories")
                    external_repositories = await self._load_external_repositories()
                    seen_at = datetime.now(UTC)
                    with self.database.session_factory() as session:
                        ids = self.reconciler.reconcile_external(
                            session, external_repositories, seen_at
                        )
                    upstream_by_identity = {
                        (item.source_provider, item.source_id): item
                        for item in external_repositories
                    }
                    with self.database.session_factory() as session:
                        for local_id in ids:
                            local = session.get(Repository, local_id)
                            if local is not None:
                                identity = (local.source_provider, local.source_id or "")
                                discovered_by_id[(local_id, RepositoryKind.EXTERNAL.value)] = (
                                    upstream_by_identity[identity]
                                )
                    logger.info("Loaded %d external repositories", len(external_repositories))
                except Exception as exc:
                    message = redact(str(exc))
                    discovery_errors.append(f"external: {message}")
                    logger.error("External repository discovery failed: %s", message)

            logger.info("Global synchronization will mirror %d repositories", len(discovered_by_id))
            semaphore = asyncio.Semaphore(self.settings.sync_concurrency)

            async def sync_discovered(
                repository_id: int, upstream: SourceRepository
            ) -> tuple[bool, bool]:
                async with semaphore:
                    success = await self.sync_repository(
                        repository_id, trigger="global", upstream=upstream
                    )
                if not success:
                    return False, False
                with self.database.session_factory() as session:
                    repository = session.get(Repository, repository_id)
                    return True, repository is not None and bool(repository.last_warning)

            results = await asyncio.gather(
                *(
                    sync_discovered(repository_id, upstream)
                    for (repository_id, _kind), upstream in discovered_by_id.items()
                )
            )
            succeeded = sum(success for success, _warning in results)
            failed = len(results) - succeeded
            warnings = sum(warning for _success, warning in results)

            if not discovery_errors:
                active_cache_entries = {
                    (self._cache_key(upstream, kind), directory_name)
                    for (_repository_id, kind), upstream in discovered_by_id.items()
                    for directory_name in (
                        ("repository.git", "wiki.git")
                        if self.settings.wiki_enabled and self._wiki_url(upstream) is not None
                        else ("repository.git",)
                    )
                }
                try:
                    await self.git.maintain_cache(
                        active_cache_entries, self.settings.git_cache_retention_days
                    )
                except Exception as exc:
                    logger.warning("Git mirror cache maintenance failed: %s", redact(str(exc)))

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
        upstream: SourceRepository | None = None,
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
                source_provider = repository.source_provider
                source_id = repository.source_id or str(repository.github_id)
                kind = repository.kind
                namespace = repository.destination_namespace
                destination = repository.destination_name
            run_id = self._start_run(repository_id, "repository", trigger)
            logger.info(
                "Repository synchronization started: repository_id=%d upstream=%s trigger=%s",
                repository_id,
                full_name,
                trigger,
            )
            secrets = [
                self.settings.github_token.get_secret_value(),
                self.settings.gitea_token.get_secret_value(),
            ]
            try:
                if upstream is None:
                    if source_provider == "github":
                        upstream = await self.github.get_repository(full_name)
                        if upstream.github_id != github_id:
                            raise RuntimeError(
                                "Upstream path now refers to a different GitHub repository ID; "
                                "refusing sync"
                            )
                    else:
                        upstream = next(
                            (
                                item
                                for item in await self._load_external_repositories()
                                if item.source_provider == source_provider
                                and item.source_id == source_id
                            ),
                            None,
                        )
                        if upstream is None:
                            raise RuntimeError(
                                f"External source {source_provider}/{source_id} is no longer "
                                "present in the sources file"
                            )
                if upstream.source_provider != source_provider or upstream.source_id != source_id:
                    raise RuntimeError("Resolved source identity changed; refusing sync")
                source_token = self._source_token(upstream)
                secrets.append(source_token)
                source_username = upstream.source_username
                requested_destination = destination
                fallback_destination: str | None = None
                if kind == RepositoryKind.STARRED.value:
                    if github_id is None:
                        raise RuntimeError("A starred GitHub repository is missing its stable ID")
                    preferred = destination_name(upstream.owner, upstream.name, github_id, kind)
                    collision_safe = collision_destination_name(
                        upstream.owner, upstream.name, github_id, kind
                    )
                    if destination == preferred:
                        fallback_destination = collision_safe
                    elif destination == collision_safe:
                        requested_destination = preferred
                        fallback_destination = collision_safe

                logger.info(
                    "Preparing destination: repository_id=%d namespace=%s name=%s",
                    repository_id,
                    namespace,
                    requested_destination,
                )
                destination_repo = await self.gitea.ensure_repository(
                    namespace=namespace,
                    name=requested_destination,
                    github_id=github_id,
                    kind=kind,
                    source_full_name=upstream.full_name,
                    source_description=upstream.description,
                    private=self.settings.destination_private,
                    fallback_name=fallback_destination,
                    source_provider=source_provider,
                    source_id=source_id,
                )
                destination = destination_repo.name
                logger.info(
                    "Destination ready: repository_id=%d namespace=%s name=%s",
                    repository_id,
                    namespace,
                    destination,
                )
                with self.database.session_factory.begin() as session:
                    repository = session.get(Repository, repository_id)
                    assert repository is not None
                    repository.destination_name = destination
                    repository.destination_url = destination_repo.html_url
                user = await self.gitea.authenticated_user()
                self.gitea_status = "connected"
                logger.info(
                    "Mirroring Git refs: repository_id=%d upstream=%s", repository_id, full_name
                )
                await self.git.mirror(
                    source_url=upstream.clone_url,
                    source_token=source_token,
                    source_username=source_username,
                    destination_url=destination_repo.clone_url,
                    destination_token=self.settings.gitea_token.get_secret_value(),
                    destination_username=str(user["login"]),
                    cache_key=self._cache_key(upstream, kind),
                    repository_label=full_name,
                )
                logger.info(
                    "Git refs mirrored: repository_id=%d upstream=%s", repository_id, full_name
                )
                if upstream.default_branch:
                    await self.gitea.set_default_branch(
                        namespace, destination, upstream.default_branch
                    )
                    logger.info(
                        "Default branch applied: repository_id=%d branch=%s",
                        repository_id,
                        upstream.default_branch,
                    )
                wiki_url = self._wiki_url(upstream)
                if self.settings.wiki_enabled and wiki_url is not None:
                    logger.info(
                        "Checking wiki: repository_id=%d upstream=%s", repository_id, full_name
                    )
                    if source_username == "x-access-token":
                        has_wiki_content = await self.git.remote_has_refs(wiki_url, source_token)
                    else:
                        has_wiki_content = await self.git.remote_has_refs(
                            wiki_url, source_token, source_username
                        )
                    if has_wiki_content:
                        logger.info(
                            "Mirroring wiki: repository_id=%d upstream=%s",
                            repository_id,
                            full_name,
                        )
                        await self.gitea.enable_wiki(namespace, destination)
                        if await self.gitea.initialize_wiki_if_empty(namespace, destination):
                            logger.info(
                                "Initialized destination wiki: repository_id=%d", repository_id
                            )
                        await self.git.mirror_wiki(
                            source_url=wiki_url,
                            source_token=source_token,
                            source_username=source_username,
                            destination_url=destination_repo.wiki_clone_url,
                            destination_token=self.settings.gitea_token.get_secret_value(),
                            destination_username=str(user["login"]),
                            cache_key=self._cache_key(upstream, kind),
                            repository_label=f"{full_name} wiki",
                        )
                        logger.info(
                            "Mirrored wiki for %s repository %s", source_provider, full_name
                        )
                    else:
                        logger.info(
                            "Skipped configured but empty wiki for %s repository %s",
                            source_provider,
                            upstream.full_name,
                        )
                release_warnings: list[str] = []
                external_releases_enabled = (
                    isinstance(upstream, ExternalRepository) and upstream.releases_enabled
                )
                if self.settings.releases_enabled and (
                    source_provider == "github" or external_releases_enabled
                ):
                    external_release_client: ExternalReleaseClient | None = None
                    try:
                        logger.info(
                            "Mirroring releases: repository_id=%d upstream=%s",
                            repository_id,
                            full_name,
                        )
                        release_mirror = self.release_mirror
                        mirror_assets = self.settings.release_assets_enabled
                        if isinstance(upstream, ExternalRepository):
                            external_release_client = ExternalReleaseClient(
                                upstream,
                                source_token,
                                self.settings.api_timeout_seconds,
                                self.settings.release_asset_timeout_seconds,
                            )
                            release_mirror = ReleaseMirrorService(
                                external_release_client, self.gitea
                            )
                            mirror_assets = mirror_assets and upstream.release_assets_enabled
                        release_warnings = await release_mirror.mirror(
                            upstream.full_name,
                            namespace,
                            destination,
                            mirror_assets=mirror_assets,
                            asset_mode=self.settings.release_asset_mode,
                        )
                        logger.info(
                            "Release mirror complete: repository_id=%d warnings=%d",
                            repository_id,
                            len(release_warnings),
                        )
                    except Exception as exc:
                        message = redact(str(exc), secrets)[:1000]
                        release_warnings.append(f"release mirror failed: {message}")
                        logger.warning(
                            "Release mirror for %s repository %s failed after the primary "
                            "repository was preserved: %s",
                            source_provider,
                            upstream.full_name,
                            message,
                        )
                    finally:
                        if external_release_client is not None:
                            await external_release_client.close()
                package_warnings: list[str] = []
                if self.settings.packages_enabled and kind == RepositoryKind.OWNED.value:
                    assert github_id is not None
                    if self.container_mirror is None:
                        raise RuntimeError("Container package mirror was not initialized")
                    logger.info(
                        "Mirroring container packages: repository_id=%d upstream=%s",
                        repository_id,
                        full_name,
                    )
                    package_warnings = await self.container_mirror.mirror(
                        repository_id,
                        github_id,
                        namespace,
                        destination,
                        str(user["login"]),
                    )
                    logger.info(
                        "Container package mirror complete: repository_id=%d warnings=%d",
                        repository_id,
                        len(package_warnings),
                    )
                warning_message = self._warning_message([*release_warnings, *package_warnings])
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
                else:
                    logger.info(
                        "Repository synchronization completed: repository_id=%d upstream=%s",
                        repository_id,
                        full_name,
                    )
                return True
            except Exception as exc:
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

    def _source_token(self, upstream: SourceRepository) -> str:
        if isinstance(upstream, ExternalRepository):
            return upstream.source_token()
        return self.settings.github_token.get_secret_value()

    async def _load_external_repositories(self) -> list[ExternalRepository]:
        repositories: list[ExternalRepository] = []
        identities: set[tuple[str, str]] = set()
        for configured in self.external_sources.load():
            token = configured.source_token()
            client = ExternalReleaseClient(
                configured,
                token,
                self.settings.api_timeout_seconds,
                self.settings.release_asset_timeout_seconds,
            )
            try:
                repository = await client.get_repository()
            finally:
                await client.close()
            identity = (repository.source_provider, repository.source_id)
            if identity in identities:
                raise RuntimeError(
                    "External provider returned a duplicate repository identity: "
                    f"{repository.source_provider}/{repository.source_id}"
                )
            identities.add(identity)
            repositories.append(repository)
        return repositories

    @staticmethod
    def _wiki_url(upstream: SourceRepository) -> str | None:
        if isinstance(upstream, UpstreamRepository) and not upstream.has_wiki:
            return None
        return upstream.wiki_clone_url

    @staticmethod
    def _cache_key(upstream: SourceRepository, kind: str) -> str:
        if upstream.source_provider == "github":
            return f"{kind}-{upstream.source_id}"
        return f"external-{upstream.source_provider}-{upstream.source_id}"

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
                "external": total_by_kind.get(RepositoryKind.EXTERNAL.value, 0),
                "active": total_by_status.get(RepositoryStatus.ACTIVE.value, 0),
                "syncing": total_by_status.get(RepositoryStatus.SYNCING.value, 0),
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
