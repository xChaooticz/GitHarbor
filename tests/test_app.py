from __future__ import annotations

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
    } <= paths
