# Architecture decisions

## Preservation is a state transition

Discovery results are reconciled independently for GitHub owned, GitHub starred, and external sets.
A successful empty result is meaningful; a failed request or invalid external file is not.
Consequently, GitHarbor changes missing records only after the corresponding complete listing
succeeds. Missing owned and external repositories become `unavailable`, while missing stars become
`unstarred`. There is intentionally no repository deletion method in the Gitea client.

## Identity and destination stability

The database identity is the source provider, its stable source ID, and the repository kind. GitHub
rows retain their numeric repository ID for package and compatibility logic. External entries use
the stable numeric ID returned by the Forgejo or GitLab repository API, with an optional explicit
compatibility override in the versioned TOML inventory. Full names are metadata, so a rename or
transfer updates upstream fields without creating a new destination. Owned
destinations retain the familiar repository name. Starred destinations normally use `owner--name`;
the `--gh<github_id>` suffix is reserved for an actual normalized-name or Gitea-path collision.
Legacy always-suffixed destinations are renamed through Gitea's API only when the old management
marker matches and the clean path is available. Other assignments remain stable across upstream
renames and transfers.

## Push ownership proof

A repository path existing in Gitea is insufficient proof that GitHarbor may overwrite it. Creation
copies the source description and appends readable provenance plus a deterministic marker containing
the provider, source ID, and kind (with the legacy `github-id` marker retained for GitHub). Every
later push checks those values before refreshing the description from the source. This makes manual
marker removal fail safely and prevents an unrelated repository from being
selected by a name collision.

## Mirroring and credentials

Each source has a persistent bare mirror cache. The first synchronization clones it; later runs fetch
only changed objects and prune deleted refs. Entries are validated before reuse, corrupt entries are
rebuilt atomically, active entries receive automatic Git garbage collection, and repositories absent
from successful discovery expire after the configured retention period. `--mirror` accurately
synchronizes fetched refs, including forced updates and deletion. Provider-owned `refs/pull/*` refs
are excluded by default because very large projects can expose hundreds of thousands of them. An
explicit preservation mode fetches them and transactionally moves them to
`refs/githarbor/github-pull/*`. Gerrit-style `refs/for/*` refs always move to
`refs/githarbor/gerrit-for/*` because Gitea reserves both source namespaces for its own pull-request
handling. After the push, the Gitea API applies the source provider's current default branch.
Tokens are supplied through a short-lived askpass environment and redacted from errors. Git
subprocesses run in isolated process groups so timeout and cancellation terminate their HTTP and
pack helpers as well as the parent command.

Git LFS is transferred before refs are published: the mirror fetches all LFS objects reachable from
its configured ref namespace, uploads them to a named destination remote, then performs the mirror
push with hooks disabled. A failed LFS step prevents the ref push. For HTTP remotes, command-local
LFS URLs are derived from the trusted API clone URLs, preventing repository-controlled `.lfsconfig`
from redirecting an askpass credential. Locks and unreachable server-side LFS objects are not part
of the Git ref graph and are deliberately outside this guarantee.

Wikis are separate Git repositories rather than part of the primary repository. For GitHub,
GitHarbor uses the upstream `has_wiki` capability as a cheap first check and derives the standard
wiki URL. External sources require an explicit `wiki_url`, so omission performs no probe. In either
case, GitHarbor verifies that the wiki remote has refs, skips an empty wiki, enables Gitea's native
wiki unit, and mirror-pushes the complete wiki history. Wiki repositories do not use the primary
repository's LFS transfer path.

External Forgejo and GitLab sources are deliberately file-driven rather than inferred from arbitrary
URLs. Clone and optional wiki URLs are validated, credentials are named indirectly through an
environment variable, and the file is reloaded for every run. An omitted wiki URL is an explicit
skip. Provider adapters map Forgejo and GitLab release metadata into the same guarded reconciliation
path. Forgejo attachments require a declared byte size; GitLab asset links without one are retained
as warnings rather than weakening attachment limits. Package API mirroring remains GitHub-specific,
and tags remain included in the provider-neutral Git mirror.

Releases are provider API resources layered on top of the mirrored tags. After Git and wiki work
succeeds, GitHarbor adapts GitHub, Forgejo, or GitLab metadata into one guarded Gitea reconciliation
path. A hidden, machine-readable marker in each managed release body stores the stable source
release ID and asset mapping while leaving the visible source body intact. An unmarked same-tag
release is treated as user-owned and is never overwritten. Unchanged managed releases need no
metadata PATCH, and embedded Gitea attachment lists avoid redundant per-release reads. Managed
assets are streamed through one isolated temporary file at a time, checked against Gitea's
advertised attachment limit, and validated by byte count plus an available GitHub SHA-256 digest.
Stale assets are removed only when their recorded ID, name, and size still match; ambiguity preserves
data and emits a warning.

Asset failures are partial rather than destructive failures. The successful Git, LFS, wiki, and
release metadata remain current, the repository remains active, and a durable warning identifies
each skipped asset for retry. Releases missing from a source listing are retained because token
visibility—especially for drafts—may be incomplete and preservation is safer than deletion.

Optional mirror layers are controlled independently. Disabling wiki, release, or release-asset
mirroring performs no cleanup, so previously preserved data remains in Gitea. In `latest` asset mode,
GitHarbor asks the source provider for its latest published non-draft, non-prerelease release. All
visible release metadata is still reconciled, but safely managed assets on every other release are
treated as stale.
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

One in-process lock covers global discovery; one lock per database repository ID protects mirror
work. A bounded worker pool processes up to `SYNC_CONCURRENCY` repositories during a global run.
Run rows are written before work starts. On process restart, lingering `running` runs and `syncing`
repositories become explicit failures and can be retried. SQLite uses WAL, foreign keys, and a busy
timeout. A single application replica is a deliberate constraint.

## Scheduling

An asyncio loop is sufficient for one periodic job and avoids a queue, broker, or worker service.
The next run is calculated from process time. Persistent schedule semantics are unnecessary because
startup synchronization is enabled by default and run history is durable.
