from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session


def run_migrations(database_url: str, revision: str = "head") -> None:
    if database_url.startswith("sqlite:///"):
        path = Path(database_url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
    script_location = Path.cwd() / "alembic"
    if not script_location.is_dir():
        script_location = Path(__file__).parent.parent / "alembic"
    if not script_location.is_dir():
        raise RuntimeError("Alembic migration directory was not found")
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)
