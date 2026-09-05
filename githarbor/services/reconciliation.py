from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from githarbor.clients.github import UpstreamRepository
from githarbor.external_sources import ExternalRepository
from githarbor.models import Repository, RepositoryKind, RepositoryStatus
from githarbor.services.naming import collision_destination_name, destination_name


class Reconciler:
    def reconcile(
        self,
        session: Session,
        discovered: list[UpstreamRepository],
        kind: RepositoryKind,
        namespace: str,
        seen_at: datetime,
    ) -> list[int]:
        seen_ids: set[int] = set()
        local_ids: list[int] = []
        for upstream in discovered:
            if upstream.github_id in seen_ids:
                continue
            seen_ids.add(upstream.github_id)
            local = session.scalar(
                select(Repository).where(
                    Repository.github_id == upstream.github_id, Repository.kind == kind.value
                )
            )
            if local is None:
                name = destination_name(
                    upstream.owner, upstream.name, upstream.github_id, kind.value
                )
                collision = session.scalar(
                    select(Repository).where(
                        Repository.destination_namespace == namespace,
                        Repository.destination_name == name,
                    )
                )
                if collision is not None:
                    name = collision_destination_name(
                        upstream.owner, upstream.name, upstream.github_id, kind.value
                    )
                local = Repository(
                    github_id=upstream.github_id,
                    source_provider="github",
                    source_id=str(upstream.github_id),
                    kind=kind.value,
                    destination_namespace=namespace,
                    destination_name=name,
                    first_discovered_at=seen_at,
                    last_seen_at=seen_at,
                    status=RepositoryStatus.ACTIVE.value,
                    currently_starred=kind is RepositoryKind.STARRED,
                    upstream_owner=upstream.owner,
                    upstream_name=upstream.name,
                    upstream_full_name=upstream.full_name,
                    upstream_url=upstream.html_url,
                    clone_url=upstream.clone_url,
                    wiki_clone_url=upstream.wiki_clone_url if upstream.has_wiki else None,
                )
                session.add(local)
            self._update_metadata(local, upstream, seen_at)
            if local.status != RepositoryStatus.ARCHIVED.value:
                local.status = RepositoryStatus.ACTIVE.value
            local.currently_starred = kind is RepositoryKind.STARRED
            session.flush()
            local_ids.append(local.id)

        known = session.scalars(select(Repository).where(Repository.kind == kind.value)).all()
        for local in known:
            if local.github_id in seen_ids:
                continue
            if kind is RepositoryKind.STARRED:
                local.currently_starred = False
                local.status = RepositoryStatus.UNSTARRED.value
            else:
                local.status = RepositoryStatus.UNAVAILABLE.value
        session.commit()
        return local_ids

    def reconcile_external(
        self,
        session: Session,
        discovered: list[ExternalRepository],
        seen_at: datetime,
    ) -> list[int]:
        seen_ids: set[tuple[str, str]] = set()
        local_ids: list[int] = []
        for upstream in discovered:
            identity = (upstream.source_provider, upstream.source_id)
            if identity in seen_ids:
                continue
            seen_ids.add(identity)
            local = session.scalar(
                select(Repository).where(
                    Repository.source_provider == upstream.source_provider,
                    Repository.source_id == upstream.source_id,
                    Repository.kind == RepositoryKind.EXTERNAL.value,
                )
            )
            if local is None:
                collision = session.scalar(
                    select(Repository).where(
                        Repository.destination_namespace == upstream.destination_namespace,
                        Repository.destination_name == upstream.destination_name,
                    )
                )
                if collision is not None:
                    raise ValueError(
                        "External destination is already assigned to another source: "
                        f"{upstream.destination_namespace}/{upstream.destination_name}"
                    )
                local = Repository(
                    github_id=None,
                    source_provider=upstream.source_provider,
                    source_id=upstream.source_id,
                    node_id=None,
                    kind=RepositoryKind.EXTERNAL.value,
                    destination_namespace=upstream.destination_namespace,
                    destination_name=upstream.destination_name,
                    first_discovered_at=seen_at,
                    last_seen_at=seen_at,
                    status=RepositoryStatus.ACTIVE.value,
                    currently_starred=False,
                    upstream_owner=upstream.owner,
                    upstream_name=upstream.name,
                    upstream_full_name=upstream.full_name,
                    upstream_url=upstream.html_url,
                    clone_url=upstream.clone_url,
                )
                session.add(local)
            self._update_external_metadata(local, upstream, seen_at)
            if local.status != RepositoryStatus.ARCHIVED.value:
                local.status = RepositoryStatus.ACTIVE.value
            session.flush()
            local_ids.append(local.id)

        known = session.scalars(
            select(Repository).where(Repository.kind == RepositoryKind.EXTERNAL.value)
        ).all()
        for local in known:
            identity = (local.source_provider, local.source_id or "")
            if identity not in seen_ids:
                local.status = RepositoryStatus.UNAVAILABLE.value
        session.commit()
        return local_ids

    @staticmethod
    def _update_metadata(
        local: Repository, upstream: UpstreamRepository, seen_at: datetime
    ) -> None:
        # Stable GitHub ID is the identity. Destination is deliberately retained on rename/transfer.
        local.node_id = upstream.node_id
        local.upstream_owner = upstream.owner
        local.upstream_name = upstream.name
        local.upstream_full_name = upstream.full_name
        local.upstream_url = upstream.html_url
        local.clone_url = upstream.clone_url
        local.source_provider = "github"
        local.source_id = str(upstream.github_id)
        local.wiki_clone_url = upstream.wiki_clone_url if upstream.has_wiki else None
        local.default_branch = upstream.default_branch
        local.upstream_private = upstream.private
        local.upstream_archived = upstream.archived
        local.upstream_fork = upstream.fork
        local.last_seen_at = seen_at

    @staticmethod
    def _update_external_metadata(
        local: Repository, upstream: ExternalRepository, seen_at: datetime
    ) -> None:
        # The file's stable ID is the identity. Destination assignment is retained after creation.
        local.upstream_owner = upstream.owner
        local.upstream_name = upstream.name
        local.upstream_full_name = upstream.full_name
        local.upstream_url = upstream.html_url
        local.clone_url = upstream.clone_url
        local.wiki_clone_url = upstream.wiki_clone_url
        local.default_branch = upstream.default_branch
        local.upstream_private = upstream.private
        local.upstream_archived = upstream.archived
        local.upstream_fork = upstream.fork
        local.last_seen_at = seen_at
