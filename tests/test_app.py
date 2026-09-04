from __future__ import annotations

from inspect import iscoroutinefunction

from fastapi.routing import APIRoute

from githarbor.app import create_app


def test_application_constructs_and_registers_core_routes() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert {
        "/",
        "/api/health",
        "/api/status",
        "/api/repositories",
        "/api/repositories/{repository_id}",
        "/api/sync",
        "/api/repositories/{repository_id}/sync",
        "/admin",
        "/actions/sync",
        "/actions/repositories/{repository_id}/sync",
        "/actions/admin/stop-sync",
        "/actions/admin/purge-organization",
    } <= paths


def test_sync_trigger_routes_run_on_the_application_event_loop() -> None:
    app = create_app()
    routes = {route.path: route for route in app.routes if isinstance(route, APIRoute)}

    for path in (
        "/api/sync",
        "/api/repositories/{repository_id}/sync",
        "/actions/sync",
        "/actions/repositories/{repository_id}/sync",
    ):
        assert iscoroutinefunction(routes[path].endpoint)
