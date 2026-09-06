from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text

from githarbor import __version__
from githarbor.clients.gitea import GiteaClient
from githarbor.clients.github import GitHubClient
from githarbor.clients.github_packages import GitHubPackagesClient
from githarbor.config import Settings, get_settings
from githarbor.database import Database, run_migrations
from githarbor.logging import configure_logging
from githarbor.models import Repository, SyncRun
from githarbor.services.containers import ContainerMirrorService
from githarbor.services.git import GitMirror
from githarbor.services.registry import RegistryCredentials, SkopeoClient
from githarbor.services.scheduler import Scheduler
from githarbor.services.sync import SyncService

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
logger = logging.getLogger(__name__)


def create_app(provided_settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = provided_settings or get_settings()
        configure_logging(settings.log_level)
        run_migrations(settings.database_url)
        database = Database(settings.database_url)
        github_token = settings.github_token.get_secret_value()
        github = GitHubClient(
            settings.github_api_base,
            github_token,
            settings.github_username,
            settings.api_timeout_seconds,
            settings.release_asset_timeout_seconds,
        )
        gitea = GiteaClient(
            settings.gitea_api_base,
            settings.gitea_token.get_secret_value(),
            settings.api_timeout_seconds,
            settings.release_asset_timeout_seconds,
        )
        github_packages: GitHubPackagesClient | None = None
        container_mirror: ContainerMirrorService | None = None
        if settings.packages_enabled:
            github_packages = GitHubPackagesClient(
                settings.github_api_base,
                github_token,
                settings.github_username,
                settings.api_timeout_seconds,
            )
            container_mirror = ContainerMirrorService(
                database,
                github_packages,
                gitea,
                SkopeoClient(settings.package_transfer_timeout_seconds),
                RegistryCredentials(
                    settings.github_container_registry,
                    settings.github_username,
                    github_token,
                ),
                settings.gitea_registry,
                settings.gitea_token.get_secret_value(),
                destination_tls_verify=settings.gitea_registry_tls_verify,
                image_mode=settings.container_image_mode,
                max_bytes=settings.package_max_bytes,
            )
        service = SyncService(
            settings,
            database,
            github,
            gitea,
            GitMirror(
                timeout_seconds=settings.git_timeout_seconds,
                lfs_enabled=settings.git_lfs_enabled,
                cache_path=settings.git_cache_path,
                pull_refs_enabled=settings.git_pull_refs_enabled,
            ),
            container_mirror,
        )
        scheduler = Scheduler(service, settings.sync_interval, settings.sync_on_startup)
        app.state.settings = settings
        app.state.database = database
        app.state.sync_service = service
        app.state.scheduler = scheduler
        scheduler.start()
        logger.info(
            "GitHarbor started: version=%s sync_on_startup=%s sync_interval_seconds=%d",
            __version__,
            settings.sync_on_startup,
            settings.sync_interval,
        )
        yield
        await scheduler.stop()
        await service.shutdown()
        if github_packages is not None:
            await github_packages.close()
        await github.close()
        await gitea.close()
        database.engine.dispose()

    application = FastAPI(
        title="GitHarbor",
        description="Your self-hosted safe harbor for Git repositories.",
        version=__version__,
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def dependencies(request: Request) -> tuple[Database, SyncService, Scheduler]:
        return (
            request.app.state.database,
            request.app.state.sync_service,
            request.app.state.scheduler,
        )

    def require_admin_actions(request: Request, submitted_token: str) -> Settings:
        settings: Settings = request.app.state.settings
        if not settings.admin_actions_available:
            raise HTTPException(status_code=404, detail="Administrative actions are disabled")
        configured_token = settings.admin_actions_token
        assert configured_token is not None
        if not hmac.compare_digest(submitted_token, configured_token.get_secret_value()):
            raise HTTPException(status_code=403, detail="Invalid administrative action token")
        return settings

    @application.get("/api/health")
    def health(request: Request) -> dict[str, str]:
        database, _, _ = dependencies(request)
        try:
            with database.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "version": __version__}

    @application.get("/api/status")
    def api_status(request: Request) -> dict[str, Any]:
        _, service, scheduler = dependencies(request)
        return service.status(scheduler.next_sync)

    @application.get("/api/repositories")
    def api_repositories(
        request: Request,
        kind: str | None = None,
        repository_status: Annotated[str | None, Query(alias="status")] = None,
        search: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        database, _, _ = dependencies(request)
        statement = select(Repository)
        count_statement = select(func.count()).select_from(Repository)
        filters = []
        if kind:
            filters.append(Repository.kind == kind)
        if repository_status:
            filters.append(Repository.status == repository_status)
        if search:
            filters.append(Repository.upstream_full_name.contains(search))
        statement = (
            statement.where(*filters)
            .order_by(Repository.upstream_full_name)
            .limit(limit)
            .offset(offset)
        )
        count_statement = count_statement.where(*filters)
        with database.session_factory() as session:
            repositories = session.scalars(statement).all()
            total = session.scalar(count_statement) or 0
        return {"items": [item.as_dict() for item in repositories], "total": total}

    @application.get("/api/repositories/{repository_id}")
    def api_repository(repository_id: int, request: Request) -> dict[str, Any]:
        database, _, _ = dependencies(request)
        with database.session_factory() as session:
            repository = session.get(Repository, repository_id)
            if repository is None:
                raise HTTPException(status_code=404, detail="Repository not found")
            runs = session.scalars(
                select(SyncRun)
                .where(SyncRun.repository_id == repository_id)
                .order_by(SyncRun.started_at.desc())
                .limit(100)
            ).all()
        return {**repository.as_dict(), "sync_history": [run.as_dict() for run in runs]}

    @application.post("/api/sync", status_code=status.HTTP_202_ACCEPTED)
    async def api_sync(request: Request) -> dict[str, str]:
        _, service, _ = dependencies(request)
        if not service.start_global_sync("api"):
            raise HTTPException(
                status_code=409, detail="A global synchronization is already running"
            )
        return {"status": "accepted"}

    @application.post(
        "/api/repositories/{repository_id}/sync", status_code=status.HTTP_202_ACCEPTED
    )
    async def api_repository_sync(repository_id: int, request: Request) -> dict[str, str]:
        database, service, _ = dependencies(request)
        with database.session_factory() as session:
            if session.get(Repository, repository_id) is None:
                raise HTTPException(status_code=404, detail="Repository not found")
        if not service.start_repository_sync(repository_id, "api"):
            raise HTTPException(status_code=409, detail="This repository is already syncing")
        return {"status": "accepted"}

    @application.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        _, service, scheduler = dependencies(request)
        dashboard_status = service.status(scheduler.next_sync)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "status": dashboard_status,
                "version": __version__,
                "admin_actions_available": request.app.state.settings.admin_actions_available,
                "auto_refresh": True,
                "sync_running": dashboard_status["sync_running"],
            },
        )

    @application.get("/repositories", response_class=HTMLResponse)
    def repositories_page(
        request: Request,
        kind: str = "",
        repository_status: Annotated[str, Query(alias="status")] = "",
        search: str = "",
    ) -> HTMLResponse:
        database, service, _ = dependencies(request)
        statement = select(Repository)
        if kind:
            statement = statement.where(Repository.kind == kind)
        if repository_status:
            statement = statement.where(Repository.status == repository_status)
        if search:
            statement = statement.where(Repository.upstream_full_name.contains(search))
        with database.session_factory() as session:
            repositories = session.scalars(statement.order_by(Repository.upstream_full_name)).all()
        return templates.TemplateResponse(
            request,
            "repositories.html",
            {
                "repositories": repositories,
                "filters": {"kind": kind, "status": repository_status, "search": search},
                "admin_actions_available": request.app.state.settings.admin_actions_available,
                "auto_refresh": True,
                "sync_running": service.is_running,
            },
        )

    @application.get("/repositories/{repository_id}", response_class=HTMLResponse)
    def repository_page(repository_id: int, request: Request) -> HTMLResponse:
        database, service, _ = dependencies(request)
        with database.session_factory() as session:
            repository = session.get(Repository, repository_id)
            if repository is None:
                raise HTTPException(status_code=404, detail="Repository not found")
            runs = session.scalars(
                select(SyncRun)
                .where(SyncRun.repository_id == repository_id)
                .order_by(SyncRun.started_at.desc())
                .limit(100)
            ).all()
        return templates.TemplateResponse(
            request,
            "repository.html",
            {
                "repository": repository,
                "runs": runs,
                "admin_actions_available": request.app.state.settings.admin_actions_available,
                "auto_refresh": True,
                "sync_running": service.is_running,
            },
        )

    @application.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request, notice: str = "") -> HTMLResponse:
        settings: Settings = request.app.state.settings
        if not settings.admin_actions_available:
            raise HTTPException(status_code=404, detail="Administrative actions are disabled")
        _, service, _ = dependencies(request)
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "admin_actions_available": True,
                "namespaces": [
                    settings.gitea_owned_namespace,
                    settings.gitea_starred_namespace,
                ],
                "sync_running": service.is_running,
                "notice": notice,
            },
        )

    @application.post("/actions/sync")
    async def web_sync(request: Request) -> RedirectResponse:
        _, service, _ = dependencies(request)
        service.start_global_sync("web")
        return RedirectResponse("/", status_code=303)

    @application.post("/actions/repositories/{repository_id}/sync")
    async def web_repository_sync(
        repository_id: int, request: Request, submit: Annotated[str, Form()] = "sync"
    ) -> RedirectResponse:
        del submit
        database, service, _ = dependencies(request)
        with database.session_factory() as session:
            if session.get(Repository, repository_id) is None:
                raise HTTPException(status_code=404, detail="Repository not found")
        service.start_repository_sync(repository_id, "web")
        return RedirectResponse(f"/repositories/{repository_id}", status_code=303)

    @application.post("/actions/admin/stop-sync")
    async def web_stop_sync(
        request: Request, admin_token: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_admin_actions(request, admin_token)
        _, service, _ = dependencies(request)
        stopped = await service.stop_active_syncs()
        return RedirectResponse(
            f"/admin?notice=Stopped+{stopped}+active+synchronization+task%28s%29",
            status_code=303,
        )

    @application.post("/actions/admin/purge-organization")
    async def web_purge_organization(
        request: Request,
        admin_token: Annotated[str, Form()],
        namespace: Annotated[str, Form()],
        confirmation: Annotated[str, Form()],
    ) -> RedirectResponse:
        settings = require_admin_actions(request, admin_token)
        allowed_namespaces = {
            settings.gitea_owned_namespace,
            settings.gitea_starred_namespace,
        }
        if namespace not in allowed_namespaces:
            raise HTTPException(
                status_code=400, detail="Unknown GitHarbor destination organization"
            )
        if confirmation != f"DELETE ALL REPOSITORIES IN {namespace}":
            raise HTTPException(status_code=400, detail="Deletion confirmation did not match")
        _, service, _ = dependencies(request)
        if service.is_running:
            raise HTTPException(
                status_code=409, detail="Stop synchronization before deleting repositories"
            )
        await service.gitea.delete_organization_repositories(namespace)
        return RedirectResponse(
            f"/admin?notice=Gitea+accepted+deletion+for+{namespace}.+Wait+for+it+to+finish+before+syncing.",
            status_code=303,
        )

    return application


app = create_app()
