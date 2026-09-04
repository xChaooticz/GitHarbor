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

Releases are API resources layered on top of the mirrored tags. After Git and wiki work succeeds,
GitHarbor reconciles GitHub release metadata and assets into native Gitea releases. A hidden,
machine-readable marker in each managed release body stores the stable GitHub release ID and asset
mapping while leaving the visible source body intact. An unmarked same-tag release is treated as
user-owned and is never overwritten. Managed assets are streamed through one isolated temporary
file at a time, checked against Gitea's advertised attachment limit, validated by byte count and an
available GitHub SHA-256 digest, and uploaded through the Gitea API. Stale assets are removed only
when their recorded ID, name, and size still match; ambiguity preserves data and emits a warning.

Asset failures are partial rather than destructive failures. The successful Git, LFS, wiki, and
release metadata remain current, the repository remains active, and a durable warning identifies
each skipped asset for retry. Releases missing from a GitHub listing are retained because token
visibility—especially for drafts—may be incomplete and preservation is safer than deletion.

Optional mirror layers are controlled independently. Disabling wiki, release, or release-asset
mirroring performs no cleanup, so previously preserved data remains in Gitea. In `latest` asset mode,
GitHarbor asks GitHub for the latest published non-draft, non-prerelease release. All visible release
metadata is still reconciled, but safely managed assets on every other release are treated as stale.
Cleanup begins only after the latest release's complete asset set succeeds, so a failed new upload
cannot discard the previous fallback. This explicit retention mode is the only feature-switch path
that deletes existing assets.

Container packages are owner-level registry resources, so GitHarbor limits discovery to packages
whose GitHub API metadata links them to an owned repository and links the copied Gitea package back
to that managed destination. Skopeo copies complete manifest lists directly between registries with
digest preservation. A SQLite ownership journal is written before external mutation and records
exact Gitea package-version IDs after verification. Existing unmanaged package names or tags stop
the transfer; cleanup deletes only exact recorded versions and retains anything externally changed.

The same classic `GITHUB_TOKEN` authenticates repository API/Git reads, package discovery, and
Skopeo's source-registry reads. This keeps one GitHub identity and rotation path. The standard token
has `repo` for private repository data and `read:packages` for container reads. The independent
Gitea token remains necessary for destination writes.

In `all` mode, a deterministic digest-derived tag keeps every copied manifest reachable after
mutable source tags move. In `latest` mode, the literal `latest` digest and all of its companion tags
are verified before older managed versions are considered stale. Missing or ambiguous `latest`, a
failed transfer, changed ownership evidence, or another tag referencing an old digest prevents
cleanup and produces a durable partial-run warning. Disabling package mirroring performs no cleanup.

## Concurrency and recovery

One in-process lock covers global discovery; one lock per database repository ID protects temporary
mirror work. Run rows are written before work starts. On process restart, lingering `running` runs
and `syncing` repositories become explicit failures and can be retried. SQLite uses WAL, foreign
keys, and a busy timeout. A single application replica is a deliberate constraint.

## Scheduling

An asyncio loop is sufficient for one periodic job and avoids a queue, broker, or worker service.
The next run is calculated from process time. Persistent schedule semantics are unnecessary because
startup synchronization is enabled by default and run history is durable.
