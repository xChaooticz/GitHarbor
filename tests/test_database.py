from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

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
    assert {"source_provider", "source_id", "wiki_clone_url"} <= repository_columns


def test_external_source_migration_backfills_existing_github_identity(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    run_migrations(database_url, revision="0003_container_images")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO repositories (
                    github_id, upstream_owner, upstream_name, upstream_full_name, upstream_url,
                    clone_url, kind, status, destination_namespace, destination_name,
                    upstream_private, upstream_archived, upstream_fork, currently_starred,
                    first_discovered_at, last_seen_at, created_at, updated_at
                ) VALUES (
                    123, 'octocat', 'project', 'octocat/project', 'https://github.test/o/p',
                    'https://github.test/o/p.git', 'owned', 'active', 'backups', 'project',
                    0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )
    engine.dispose()

    run_migrations(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT github_id, source_provider, source_id FROM repositories")
        ).one()
    assert row == (123, "github", "123")
