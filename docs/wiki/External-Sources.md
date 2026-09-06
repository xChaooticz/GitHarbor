# External Forgejo and GitLab sources

GitHarbor can preserve explicitly selected repositories from Forgejo and GitLab in addition to the
repositories discovered from the configured GitHub account. External sources are listed in a
versioned TOML file, so adding a repository is deliberate and reviewable. GitHarbor does not crawl
an entire Forgejo or GitLab instance.

For each entry, GitHarbor can preserve:

- Ordinary Git refs and history, including branches, tags, notes, force-updates, and ref deletions
- Git LFS objects reachable from those refs when LFS mirroring is enabled
- The complete wiki Git repository when an explicit `wiki_url` is configured
- Native release metadata, plus eligible release attachments

Issues, merge or pull requests, CI jobs, discussions, and external container packages are not
mirrored. Container-package mirroring remains limited to GitHub packages linked to owned GitHub
repositories.

## Prepare a destination namespace

`destination_namespace` is a namespace on the destination Gitea instance. A dedicated organization
such as `external-backups` is recommended because it keeps manually selected sources separate from
the GitHub-owned and GitHub-starred sets:

| Source set | Recommended Gitea organization | Configured in |
|---|---|---|
| GitHub owned | `github-backups` | `GITEA_OWNED_NAMESPACE` |
| GitHub starred | `github-archive` | `GITEA_STARRED_NAMESPACE` |
| Forgejo/GitLab | `external-backups` | Each TOML entry |

The namespace may instead be an existing writable organization or the authenticated Gitea user's
personal namespace. The Gitea token account must be able to create repositories and push there.
Using a dedicated organization is safest because GitHarbor refuses to overwrite an existing
repository without its matching management marker.

The dashboard's bulk organization-reset control applies only to the two GitHub namespaces. It does
not delete external destination namespaces.

## Create and mount the inventory

Copy the supplied example next to `docker-compose.yml`:

```sh
cp external-sources.example.toml external-sources.toml
```

Edit it and keep only the repositories you want. This is a working public Forgejo example:

```toml
version = 1

[[repositories]]
provider = "forgejo"
clone_url = "https://git.eden-emu.dev/eden-emu/eden.git"
wiki_url = "https://git.eden-emu.dev/eden-emu/eden.wiki.git"
destination_namespace = "external-backups"
```

The provider API supplies this repository's stable ID, source name, description, visibility, and
default branch. The destination name is inferred from the final component of the clone URL, so
GitHarbor creates `external-backups/eden`; no manually chosen `id` or `destination_name` is needed.

Set the container path in `.env`:

```dotenv
EXTERNAL_SOURCES_FILE=/config/external-sources.toml
```

Then uncomment the matching read-only bind mount in `docker-compose.yml`:

```yaml
volumes:
  - githarbor-data:/data
  - ./external-sources.toml:/config/external-sources.toml:ro
```

Recreate the service after changing `.env` or the Compose mount. The TOML contents themselves are
reloaded at the start of every global sync and every individual external-repository retry.

## Repository fields

Each `[[repositories]]` table accepts these fields:

| Field | Required/default | Meaning |
|---|---:|---|
| `provider` | required | `forgejo` or `gitlab` |
| `clone_url` | required | HTTP(S) Git clone URL, without embedded credentials |
| `destination_namespace` | required | Writable Gitea organization or the token user's namespace |
| `destination_name` | final clone-URL component | Override the Gitea repository name |
| `wiki_url` | unset | Separate wiki Git URL; omission means skip the wiki without probing |
| `web_url` | inferred | Source browser URL used for provenance when the API does not provide one |
| `api_url` | provider default | Forgejo `/api/v1` or GitLab `/api/v4` base on the clone host |
| `token_env` | unset | Name of an environment variable containing a read-only source token |
| `git_username` | provider default | GitLab defaults to `oauth2`; Forgejo defaults to `git` |
| `releases` | `true` | Mirror native release metadata for this repository |
| `release_assets` | `true` | Mirror supported attachments when releases are enabled |
| `id` | API result | Backward-compatible identity override; normally omit it |

The file also accepts compatibility metadata fields `description`, `default_branch`, `private`,
`archived`, and `fork`. Normally these should be omitted because current provider API metadata is
authoritative.

GitHarbor validates the whole file before using it. Unknown fields, duplicate clone URLs, duplicate
explicit identities, duplicate destination paths, invalid names, unsupported versions, and unsafe
URLs reject external discovery. Existing external records are preserved when the file is missing or
invalid; they are not incorrectly marked unavailable.

## Private repositories and tokens

Never put credentials in `clone_url`, `wiki_url`, `api_url`, or the TOML file. Name an environment
variable instead:

```toml
[[repositories]]
provider = "gitlab"
clone_url = "https://gitlab.example.com/team/project.git"
wiki_url = "https://gitlab.example.com/team/project.wiki.git"
destination_namespace = "external-backups"
token_env = "PROJECT_GITLAB_TOKEN"
```

Provide the value through `.env`; the supplied Compose service loads all entries from that file:

```dotenv
PROJECT_GITLAB_TOKEN=replace-with-read-only-source-token
```

The token needs read access to repository metadata and enabled releases through the provider API,
plus HTTPS Git, LFS, and wiki reads for the data being preserved. For GitLab, a personal access token commonly
uses `read_api` and `read_repository`. Forgejo permission names vary by server version; grant only
repository/API read access. Public repositories normally need no token.

When `token_env` is set, all authenticated source URLs must use HTTPS and `wiki_url` must have the
same origin as `clone_url`. A custom `api_url` must always have that same scheme, host, and port.
These rules prevent a source token from being sent to another origin. Token values are passed to Git
through the same temporary askpass mechanism used for GitHub and are redacted from errors.

## Identity, naming, and removal

The source API is queried during discovery for a stable repository ID and current metadata even
when the compatibility `id` override is set. Automatic IDs combine the provider instance with its
numeric ID, avoiding collisions when two servers happen to use the same number. The destination name
defaults to the final clone-URL component, which is normally the source repository name. Once
stored, the identity and destination assignment remain stable across later source renames or
transfers.

Removing an entry from a valid TOML file marks its inventory record `unavailable` after the next
global discovery. Its Gitea repository and cached mirror are retained. The cache expires only after
`GIT_CACHE_RETENTION_DAYS`; GitHarbor never deletes the Gitea repository automatically. Adding the
same source identity again reactivates the existing record.

## Wikis and releases

External wiki discovery is intentionally explicit. When `wiki_url` is absent, GitHarbor does not
guess, probe, create, or update a wiki. When supplied, the URL is checked for refs; an empty wiki is
skipped, while a populated wiki is mirrored into Gitea's native wiki with its complete history.

Release metadata is mirrored after Git refs are current. Forgejo release attachments are copied only
when the API reports a trustworthy byte size and a valid same-origin download URL. GitLab release
asset links do not include a trustworthy byte size, so GitHarbor records a durable skip warning
instead of bypassing its size safeguards. Use `release_assets = false` for a GitLab entry when those
warnings are not useful. Release tags are still preserved through the Git mirror even when
`releases = false`.

Global `WIKI_ENABLED`, `RELEASES_ENABLED`, and `RELEASE_ASSETS_ENABLED` settings remain the outer
switches. A per-entry setting can disable a layer but cannot enable it when the corresponding global
setting is off.

## Incremental synchronization and storage

External repositories use the same persistent bare-mirror cache as GitHub repositories. The first
run clones the source into `GIT_CACHE_PATH`; later runs fetch only changed Git objects, prune deleted
refs, and push the resulting mirror to Gitea. The cache is an operational copy, while Gitea is the
preserved destination, so storage exists in both places. You may delete the cache while GitHarbor is
stopped to reclaim space, but the next run must download the full source history again.

Provider-owned `refs/pull/*` refs follow the global `GIT_PULL_REFS_ENABLED` policy and are excluded
by default. See [Configuration](Configuration#provider-owned-pull-refs).

GitHarbor validates a cached mirror before use and rebuilds it automatically if it is invalid or
corrupt. Active entries receive `git gc --auto`, and cache directories for sources absent from a
successful discovery are removed after `GIT_CACHE_RETENTION_DAYS`. Global runs process up to
`SYNC_CONCURRENCY` repositories simultaneously, so tune concurrency to the available CPU, memory,
disk I/O, network bandwidth, and provider rate limits.

## Verify an external source

After a sync, confirm that:

1. The repository appears in its configured Gitea namespace with an `active` status in GitHarbor.
2. Branches and tags match the source, and Gitea opens the correct default branch.
3. `git lfs pull` and `git lfs fsck` succeed for an LFS repository cloned from Gitea.
4. A configured populated wiki appears in Gitea; an omitted wiki remains untouched.
5. Native releases appear with supported attachments, or a clear **Last warning** explains a safe
   asset skip.

See [Troubleshooting](https://github.com/xChaooticz/GitHarbor/wiki/Troubleshooting) for file,
credential, cache, wiki, and release diagnostics.
