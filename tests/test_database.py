from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from githarbor.database import run_migrations


def test_migrations_create_versioned_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migrated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    run_migrations(database_url)
    inspector = inspect(create_engine(database_url))
    tables = set(inspector.get_table_names())
    assert {"alembic_version", "container_images", "repositories", "sync_runs"} <= tables
    repository_columns = {column["name"] for column in inspector.get_columns("repositories")}
    assert "last_warning" in repository_columns
