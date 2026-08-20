from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from githarbor.clients.github import UpstreamRepository
from githarbor.models import Repository, RepositoryKind, RepositoryStatus
from githarbor.services.naming import destination_name


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
                local = Repository(
                    github_id=upstream.github_id,
                    kind=kind.value,
                    destination_namespace=namespace,
                    destination_name=destination_name(
                        upstream.owner, upstream.name, upstream.github_id, kind.value
                    ),
                    first_discovered_at=seen_at,
                    last_seen_at=seen_at,
                    status=RepositoryStatus.ACTIVE.value,
                    currently_starred=kind is RepositoryKind.STARRED,
                    upstream_owner=upstream.owner,
                    upstream_name=upstream.name,
                    upstream_full_name=upstream.full_name,
                    upstream_url=upstream.html_url,
                    clone_url=upstream.clone_url,
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
        local.default_branch = upstream.default_branch
        local.upstream_private = upstream.private
        local.upstream_archived = upstream.archived
        local.upstream_fork = upstream.fork
        local.last_seen_at = seen_at
