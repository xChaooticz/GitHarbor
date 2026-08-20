from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class RepositoryKind(StrEnum):
    OWNED = "owned"
    STARRED = "starred"


class RepositoryStatus(StrEnum):
    ACTIVE = "active"
    SYNCING = "syncing"
    UNAVAILABLE = "unavailable"
    UNSTARRED = "unstarred"
    ERROR = "error"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("github_id", "kind", name="uq_repository_github_id_kind"),
        UniqueConstraint("destination_namespace", "destination_name", name="uq_destination"),
        Index("ix_repositories_status_kind", "status", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(128))
    upstream_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    upstream_name: Mapped[str] = mapped_column(String(255), nullable=False)
    upstream_full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    upstream_url: Mapped[str] = mapped_column(Text, nullable=False)
    clone_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    upstream_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    upstream_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    upstream_fork: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    destination_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_url: Mapped[str | None] = mapped_column(Text)
    currently_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sync_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    runs: Mapped[list[SyncRun]] = relationship(back_populates="repository")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "github_id": self.github_id,
            "upstream_owner": self.upstream_owner,
            "upstream_name": self.upstream_name,
            "upstream_full_name": self.upstream_full_name,
            "upstream_url": self.upstream_url,
            "kind": self.kind,
            "status": self.status,
            "destination_namespace": self.destination_namespace,
            "destination_name": self.destination_name,
            "destination_url": self.destination_url,
            "currently_starred": self.currently_starred,
            "first_discovered_at": self.first_discovered_at,
            "last_seen_at": self.last_seen_at,
            "last_sync_attempt_at": self.last_sync_attempt_at,
            "last_successful_sync_at": self.last_successful_sync_at,
            "last_error": self.last_error,
            "upstream_private": self.upstream_private,
            "upstream_archived": self.upstream_archived,
            "default_branch": self.default_branch,
        }


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_repository_started", "repository_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    discovered_owned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discovered_starred: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    repository: Mapped[Repository | None] = relationship(back_populates="runs")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repository_id": self.repository_id,
            "scope": self.scope,
            "trigger": self.trigger,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "discovered_owned": self.discovered_owned,
            "discovered_starred": self.discovered_starred,
            "succeeded": self.succeeded,
            "failed": self.failed,
        }
