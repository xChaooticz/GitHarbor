# Architecture decisions

## Preservation is a state transition

Discovery results are reconciled independently for owned and starred sets. A successful empty result
is meaningful; a failed request is not. Consequently, GitHarbor changes missing records only after
the corresponding complete paginated listing succeeds. Missing owned repositories become
`unavailable`, while missing stars become `unstarred`. There is intentionally no repository deletion
method in the Gitea client.

## Identity and destination stability

The database identity is GitHub's numeric repository ID plus the owned/starred kind. Full names are
metadata, so a rename or transfer updates upstream fields without creating a new destination. Owned
destinations retain the familiar repository name. Starred destinations use
`owner--name--gh<github_id>`, which is recognizable and collision-proof. Destination assignments are
immutable after first discovery.

## Push ownership proof

A repository path existing in Gitea is insufficient proof that GitHarbor may overwrite it. Creation
adds `github-id` and `kind` to the description. Every later push checks both values. This makes manual
description removal fail safely and prevents an unrelated repository from being selected by a name
collision.

## Mirroring and credentials

Each operation gets a unique temporary directory and performs a fresh bare clone. This spends more
bandwidth than a persistent cache, but avoids stale/corrupt cache recovery and leaves no long-lived
upstream copy or credential configuration. `--mirror` accurately synchronizes refs, including forced
updates and deletion. Tokens are supplied through a short-lived askpass environment and redacted
from errors.

Git LFS is transferred before refs are published: the mirror fetches all LFS objects reachable from
its complete ref namespace, uploads them to a named destination remote, then performs the mirror
push with hooks disabled. A failed LFS step prevents the ref push. For HTTP remotes, command-local
LFS URLs are derived from the trusted API clone URLs, preventing repository-controlled `.lfsconfig`
from redirecting an askpass credential. Locks and unreachable server-side LFS objects are not part
of the Git ref graph and are deliberately outside this guarantee.

GitHub wikis are separate Git repositories rather than part of the primary repository. GitHarbor
uses the upstream `has_wiki` capability as a cheap first check, verifies that the wiki remote has
refs so an enabled but empty wiki can be skipped, enables Gitea's native wiki unit, and mirror-pushes
the complete wiki history. Wiki repositories do not use the primary repository's LFS transfer path.

## Concurrency and recovery

One in-process lock covers global discovery; one lock per database repository ID protects temporary
mirror work. Run rows are written before work starts. On process restart, lingering `running` runs
and `syncing` repositories become explicit failures and can be retried. SQLite uses WAL, foreign
keys, and a busy timeout. A single application replica is a deliberate v0.1 constraint.

## Scheduling

An asyncio loop is sufficient for one periodic job and avoids a queue, broker, or worker service.
The next run is calculated from process time. Persistent schedule semantics are unnecessary because
startup synchronization is enabled by default and run history is durable.
