# Configuration

GitHarbor reads environment variables at process startup. Docker Compose loads them from `.env`.
After changing a value, recreate the container rather than using a simple restart:

```sh
docker compose up -d --force-recreate githarbor
```

`GITHARBOR_IMAGE_TAG` is used by Compose rather than the application. It defaults to `latest`; set
it to a release such as `v0.7.1` when you want a reproducible deployment. The Compose file keeps a
local `build` definition, so `docker compose up -d --build` builds from the checked-out source.
`GITHARBOR_BIND_ADDRESS` and `GITHARBOR_PORT` are also Compose-only settings. They default to
`0.0.0.0` and `9005`, making the dashboard reachable from the private LAN.

## Required settings

| Variable | Meaning | Example |
|---|---|---|
| `GITHUB_TOKEN` | Classic GitHub PAT used for API, Git/LFS, release, and package reads | token value |
| `GITHUB_USERNAME` | GitHub login that created the token and owns the personal repository set | `octocat` |
| `GITEA_URL` | Gitea root URL, without `/api/v1` | `https://git.example.com` |
| `GITEA_TOKEN` | Gitea API and HTTPS Git/LFS token | token value |
| `GITEA_OWNED_NAMESPACE` | Gitea user or organization for owned repositories | `github-backups` |
| `GITEA_STARRED_NAMESPACE` | Gitea user or organization for starred repositories | `github-archive` |

See [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions)
before filling in token values.

## Optional settings

| Variable | Default | Meaning |
|---|---:|---|
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST API root; change for GitHub Enterprise Server |
| `SYNC_INTERVAL` | `6h` | Delay between scheduled runs; accepts seconds or `s`, `m`, `h`, `d` |
| `SYNC_ON_STARTUP` | `true` | Start a global discovery/sync after application startup |
| `SYNC_CONCURRENCY` | `3` | Repositories processed concurrently during a global run; `1`–`32` |
| `DATABASE_PATH` | `/data/githarbor.db` | SQLite inventory and run-history path |
| `DESTINATION_PRIVATE` | `true` | Make newly created Gitea repositories private |
| `API_TIMEOUT_SECONDS` | `30` | Timeout for one source-provider or Gitea API request; minimum 5 |
| `WIKI_ENABLED` | `true` | Detect and mirror populated configured wikis |
| `RELEASES_ENABLED` | `true` | Create and update native Gitea releases |
| `RELEASE_ASSETS_ENABLED` | `true` | Reconcile attachments for mirrored releases |
| `RELEASE_ASSET_MODE` | `all` | Keep assets for `all` releases or only the `latest` stable release |
| `RELEASE_ASSET_TIMEOUT_SECONDS` | `3600` | Timeout for each asset download or upload; minimum 30 |
| `PACKAGES_ENABLED` | `false` | Mirror container packages linked to owned GitHub repositories |
| `GITHUB_CONTAINER_REGISTRY` | `ghcr.io` | Source registry hostname, optionally with a port |
| `CONTAINER_IMAGE_MODE` | `all` | Keep every image digest or only the literal `latest` digest |
| `PACKAGE_MAX_BYTES` | `0` | Conservative estimated per-image byte limit; `0` disables it |
| `PACKAGE_TRANSFER_TIMEOUT_SECONDS` | `3600` | Timeout for each registry operation; minimum 30 |
| `GIT_LFS_ENABLED` | `true` | Fetch and upload reachable LFS objects before pushing refs |
| `GIT_TIMEOUT_SECONDS` | `3600` | Timeout for each Git or Git LFS command; minimum 30 |
| `GIT_CACHE_PATH` | `/data/git-mirrors` | Persistent bare mirrors used for incremental fetches |
| `GIT_CACHE_RETENTION_DAYS` | `30` | Days to retain cache entries absent from discovery |
| `EXTERNAL_SOURCES_FILE` | unset | TOML inventory of explicit Forgejo and GitLab repositories |
| `ADMIN_ACTIONS_ENABLED` | `false` | Enable restricted dashboard controls to stop syncs and reset an entire destination organization |
| `ADMIN_ACTIONS_TOKEN` | required when enabled | Separate secret required for each restricted action; do not reuse `GITEA_TOKEN` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

## Complete example

```dotenv
GITHARBOR_IMAGE_TAG=latest
GITHARBOR_BIND_ADDRESS=0.0.0.0
GITHARBOR_PORT=9005

GITHUB_TOKEN=replace-with-github-token
GITHUB_USERNAME=your-github-login
GITHUB_API_URL=https://api.github.com

GITEA_URL=https://gitea.example.com
GITEA_TOKEN=replace-with-gitea-token
GITEA_OWNED_NAMESPACE=github-backups
GITEA_STARRED_NAMESPACE=github-archive

SYNC_INTERVAL=6h
SYNC_ON_STARTUP=true
SYNC_CONCURRENCY=3
DATABASE_PATH=/data/githarbor.db
DESTINATION_PRIVATE=true
API_TIMEOUT_SECONDS=30
WIKI_ENABLED=true
RELEASES_ENABLED=true
RELEASE_ASSETS_ENABLED=true
RELEASE_ASSET_MODE=all
RELEASE_ASSET_TIMEOUT_SECONDS=3600
PACKAGES_ENABLED=false
GITHUB_CONTAINER_REGISTRY=ghcr.io
CONTAINER_IMAGE_MODE=all
PACKAGE_MAX_BYTES=0
PACKAGE_TRANSFER_TIMEOUT_SECONDS=3600
GIT_LFS_ENABLED=true
GIT_TIMEOUT_SECONDS=3600
GIT_CACHE_PATH=/data/git-mirrors
GIT_CACHE_RETENTION_DAYS=30
EXTERNAL_SOURCES_FILE=
ADMIN_ACTIONS_ENABLED=false
ADMIN_ACTIONS_TOKEN=
LOG_LEVEL=INFO
```

## External Forgejo and GitLab repositories

Copy `external-sources.example.toml`, add one `[[repositories]]` table per source, mount it read-only,
and set `EXTERNAL_SOURCES_FILE` to its container path. Each entry requires `provider` (`forgejo` or
`gitlab`), `clone_url`, and `destination_namespace`. GitHarbor obtains the stable repository ID,
name, description, and default branch from the provider API. `destination_name` defaults to the
repository name. An explicit `id` remains an optional compatibility override.

An external wiki is synchronized only when its separate Git clone URL is supplied as `wiki_url`.
Omitting `wiki_url` skips the wiki without probing a guessed endpoint. Optional URL overrides are
`web_url` and `api_url`; a custom `api_url` must use the same origin as `clone_url` so a source token
cannot be sent elsewhere. Compatibility fields `description`, `default_branch`, `private`,
`archived`, and `fork` are also accepted, but current provider API metadata is authoritative.

Never put a token in a URL or in this file. For private sources, set `token_env` to an environment
variable name and supply that variable to the container. GitLab's default Git username is `oauth2`;
Forgejo's is `git`; `git_username` can override it. Authenticated URLs are required to use HTTPS.
An authenticated `wiki_url` must use the same origin as `clone_url`.

The file is reloaded for every global run and individual external retry. Removing a valid entry
marks its inventory record `unavailable` but never deletes its Gitea destination. A missing or
invalid file fails external discovery without marking existing external entries unavailable.

External sources mirror complete Git refs and reachable LFS objects plus an explicitly configured
wiki. Native release metadata is enabled per entry by default. Forgejo attachments are copied when
the API provides a declared size; GitLab asset links without a trustworthy byte size are skipped
with a warning. Use `releases = false` or `release_assets = false` per entry to disable those layers.
Container packages remain GitHub-only, and release tags are always preserved as Git refs.

See [External sources](https://github.com/xChaooticz/GitHarbor/wiki/External-Sources) for a complete
field reference, Compose mount, credential examples, naming rules, and verification procedure.

`GITHUB_PACKAGES_TOKEN` is no longer a setting. Upgrading installations should delete it from
`.env` and use one classic `GITHUB_TOKEN` with `repo` and `read:packages`.

## Scheduling and concurrency

`SYNC_INTERVAL` is the delay between complete runs. Valid examples are `900`, `30m`, `6h`, and
`1d`. The schedule is in process memory; after a restart, `SYNC_ON_STARTUP=true` performs a fresh run
and the interval begins again.

Set `SYNC_ON_STARTUP=false` when the first large transfer should be started manually from the
dashboard. Scheduled runs still occur after the configured interval.

`SYNC_CONCURRENCY` bounds repository work during global synchronization. Higher values can shorten a
large run but increase memory, disk I/O, bandwidth, Gitea load, and provider API pressure. Individual
repository retries still use the same per-repository lock, and overlapping global runs are rejected.

## Visibility

`DESTINATION_PRIVATE=true` is recommended. The value is applied only when GitHarbor creates a Gitea
repository. Changing it later does not modify existing destinations, because GitHarbor deliberately
avoids reconfiguring repositories it already manages.

## Git LFS

Keep `GIT_LFS_ENABLED=true` for complete preservation. GitHarbor fetches all LFS objects reachable
from mirrored refs, uploads them to Gitea, and only then publishes Git refs. A failed LFS step leaves
the previous destination refs intact and marks that repository as an error.

Set it to `false` only if pointer-only mirrors are intentional. Gitea's LFS server must be enabled;
see [Gitea organizations](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations#lfs-prerequisite).

## Optional mirror layers

The primary Git mirror is always enabled. `WIKI_ENABLED`, `RELEASES_ENABLED`,
`RELEASE_ASSETS_ENABLED`, and `PACKAGES_ENABLED` control optional layers independently. The first
three default to `true`; package mirroring defaults to `false` because it requires a classic
`GITHUB_TOKEN` with `read:packages` and can consume substantial registry storage.

- `WIKI_ENABLED=false` skips wiki detection and mirroring.
- `RELEASES_ENABLED=false` skips release metadata and release assets.
- `RELEASE_ASSETS_ENABLED=false` keeps release metadata current but skips attachment reconciliation.
- `PACKAGES_ENABLED=false` skips container discovery and transfer.

Disabling a layer does not remove data already preserved in Gitea. If releases are disabled, the
release-assets setting and asset mode have no effect.

On later runs, GitHarbor compares each managed Gitea release with the desired metadata and skips the
update call when nothing changed. It also reuses attachment lists embedded in Gitea's release-list
response, reducing API traffic without changing reconciliation or retry behavior.

`RELEASE_ASSET_MODE=all` keeps assets on every visible release. Set it to `latest` to keep assets
only on the source provider's latest published stable release while continuing to mirror metadata
for every visible release. Drafts and prereleases are not candidates. When the provider selects a
new latest release, GitHarbor uploads its assets and safely deletes assets it previously managed
from older releases. If the new latest asset set has any failure, older assets remain until a later
retry succeeds. Unmanaged or externally changed Gitea attachments are retained with a warning.

Package mirroring currently applies only to GitHub container packages explicitly linked to an
owned repository. Starred-repository packages are intentionally excluded and may be supported by a
separate opt-in mode in a future release.

`CONTAINER_IMAGE_MODE=all` mirrors every image digest and all source tags, adding a stable
`githarbor-preserved-sha256-...` tag for each digest. `latest` selects the digest carrying the
literal `latest` tag and mirrors all tags attached to that same digest. It does not infer latest from
timestamps. After a new latest digest is copied and verified, only old tags and digests recorded as
GitHarbor-managed are removed. A missing `latest`, failed copy, changed tag, or unmanaged reference
preserves the previous data and produces a warning.

`PACKAGE_MAX_BYTES` is a client-side estimate based on manifest descriptor sizes. Gitea does not
provide its container-package limit through a standard settings endpoint, and a reverse proxy may
have its own limit, so the destination can still reject an image. Such a failure is reported as a
partial repository run and retried later. See
[Container packages](https://github.com/xChaooticz/GitHarbor/wiki/Container-Packages).

## Restricted dashboard actions

`ADMIN_ACTIONS_ENABLED=false` is the safe default. Set it to `true` only on a trusted private
network and provide a distinct, random `ADMIN_ACTIONS_TOKEN`. The **Admin** dashboard tab then asks
for that value on every operation; `GITEA_TOKEN` remains inside the GitHarbor container and is never
sent to the browser.

The stop action cancels active transfers but keeps the container and schedule running. The bulk
organization reset calls Gitea's `DELETE /orgs/{org}/repos` endpoint for exactly one of the two
configured GitHarbor destination namespaces. It deletes every repository in that organization,
including manually created repositories, and is therefore protected by an exact typed confirmation.
Use dedicated organizations for GitHarbor and keep backups before using it. Namespaces used only by
external TOML entries are not offered by this reset control.

## Timeouts

`API_TIMEOUT_SECONDS` covers individual HTTP API calls. GitHarbor retries transient API and rate
limit failures independently.

`RELEASE_ASSET_TIMEOUT_SECONDS` applies separately to each source release-asset download and Gitea
upload. Assets are processed one at a time, so temporary space needs to fit only the current asset.
Increasing this timeout does not change Gitea's attachment limit or a reverse proxy's request-body
limit.

`PACKAGE_TRANSFER_TIMEOUT_SECONDS` applies to each Skopeo inspect, copy, tag-list, or delete
operation. Large multi-platform images may need more time. Registry layers stream directly between
the source and destination through the GitHarbor container; ensure its network path can sustain the
transfer.

`GIT_TIMEOUT_SECONDS` applies to each clone, fetch, LFS transfer, and push command. Increase it when
large repositories fail at a repeatable duration. GitHarbor retries transient destination 502, 503, and
504 push failures, but the Gitea reverse proxy must also permit the transfer duration and request
size. Also confirm that the persistent cache and temporary release-asset area have enough space.

## Database and Compose volume

The supplied `docker-compose.yml` pins `DATABASE_PATH=/data/githarbor.db` and mounts the named volume
`githarbor-data` at `/data`. The same volume stores the default `/data/git-mirrors` cache. The Compose
`environment` value takes precedence over the same key in `.env`.

To use a bind mount or different container path, update both the `environment` and `volumes` entries
in `docker-compose.yml`. Ensure container UID `10001` can write the target. Never run two GitHarbor
replicas against the same SQLite file.

The first sync creates one bare source mirror per repository under `GIT_CACHE_PATH`. Later runs
validate and incrementally fetch into it, rebuild invalid entries automatically, and run
`git gc --auto` on active entries. Cache entries absent from successful discovery are removed after
`GIT_CACHE_RETENTION_DAYS`; `0` removes them immediately. Stop GitHarbor before manually removing a
cache directory. The cache can be recreated from the sources, but keeping it avoids downloading full
histories again.

## Network exposure

The supplied port mapping defaults to `0.0.0.0:9005:8000`, so devices on the same private network can
open `http://DOCKER_HOST_IP:9005`. Set `GITHARBOR_BIND_ADDRESS=127.0.0.1` for host-only access or when
an authenticated reverse proxy is the sole entry point. `GITHARBOR_PORT` changes the host-side port.
GitHarbor has no built-in login: anyone who can reach the port can view operational metadata and
trigger synchronization. Never expose it directly to the internet or an untrusted network.
