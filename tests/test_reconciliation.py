from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sqlalchemy import select

from githarbor.clients.github import UpstreamRepository
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryKind, RepositoryStatus
from githarbor.services.reconciliation import Reconciler


def memory_database() -> Database:
    database = Database("sqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    return database


def test_reconciliation_is_idempotent(upstream: UpstreamRepository, now: datetime) -> None:
    database = memory_database()
    reconciler = Reconciler()
    with database.session_factory() as session:
        first = reconciler.reconcile(session, [upstream], RepositoryKind.STARRED, "archive", now)
        second = reconciler.reconcile(session, [upstream], RepositoryKind.STARRED, "archive", now)
        assert first == second
        assert len(session.scalars(select(Repository)).all()) == 1


def test_unstar_preserves_record_and_destination(
    upstream: UpstreamRepository, now: datetime
) -> None:
    database = memory_database()
    reconciler = Reconciler()
    with database.session_factory() as session:
        repository_id = reconciler.reconcile(
            session, [upstream], RepositoryKind.STARRED, "archive", now
        )[0]
        destination = session.get(Repository, repository_id).destination_name  # type: ignore[union-attr]
        reconciler.reconcile(session, [], RepositoryKind.STARRED, "archive", now)
        preserved = session.get(Repository, repository_id)
        assert preserved is not None
        assert preserved.status == RepositoryStatus.UNSTARRED.value
        assert preserved.currently_starred is False
        assert preserved.destination_name == destination


def test_missing_owned_becomes_unavailable(upstream: UpstreamRepository, now: datetime) -> None:
    database = memory_database()
    reconciler = Reconciler()
    with database.session_factory() as session:
        repository_id = reconciler.reconcile(
            session, [upstream], RepositoryKind.OWNED, "backups", now
        )[0]
        reconciler.reconcile(session, [], RepositoryKind.OWNED, "backups", now)
        preserved = session.get(Repository, repository_id)
        assert preserved is not None
        assert preserved.status == RepositoryStatus.UNAVAILABLE.value


def test_rename_and_transfer_follow_stable_id(upstream: UpstreamRepository, now: datetime) -> None:
    database = memory_database()
    reconciler = Reconciler()
    with database.session_factory() as session:
        repository_id = reconciler.reconcile(
            session, [upstream], RepositoryKind.STARRED, "archive", now
        )[0]
        original_destination = session.get(Repository, repository_id).destination_name  # type: ignore[union-attr]
        moved = replace(
            upstream,
            owner="new-owner",
            name="new-name",
            full_name="new-owner/new-name",
            html_url="https://github.example/new-owner/new-name",
            clone_url="https://github.example/new-owner/new-name.git",
        )
        same_id = reconciler.reconcile(session, [moved], RepositoryKind.STARRED, "archive", now)[0]
        repository = session.get(Repository, repository_id)
        assert same_id == repository_id
        assert repository is not None
        assert repository.upstream_full_name == "new-owner/new-name"
        assert repository.destination_name == original_destination


def test_starred_name_uses_id_suffix_only_for_a_collision(
    upstream: UpstreamRepository, now: datetime
) -> None:
    database = memory_database()
    reconciler = Reconciler()
    colliding = replace(
        upstream,
        github_id=456,
        node_id="R_456",
        owner="octo user",
        full_name="octo user/project",
        html_url="https://github.example/octo user/project",
        clone_url="https://github.example/octo user/project.git",
    )
    with database.session_factory() as session:
        reconciler.reconcile(session, [upstream, colliding], RepositoryKind.STARRED, "archive", now)
        repositories = session.scalars(select(Repository).order_by(Repository.github_id)).all()
        assert [item.destination_name for item in repositories] == [
            "octo-user--project",
            "octo-user--project--gh456",
        ]
