# Configuration

GitHarbor reads environment variables at process startup. Docker Compose loads them from `.env`.
After changing a value, recreate the container rather than using a simple restart:

```sh
docker compose up -d --force-recreate githarbor
```

`GITHARBOR_IMAGE_TAG` is used by Compose rather than the application. It defaults to `latest`; set
it to a release such as `v0.5.1` when you want a reproducible deployment. The Compose file keeps a
local `build` definition, so `docker compose up -d --build` builds from the checked-out source.

## Required settings

| Variable | Meaning | Example |
|---|---|---|
| `GITHUB_TOKEN` | GitHub token used for API discovery and HTTPS Git/LFS reads | `github_pat_...` |
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
| `DATABASE_PATH` | `/data/githarbor.db` | SQLite inventory and run-history path |
| `DESTINATION_PRIVATE` | `true` | Make newly created Gitea repositories private |
| `API_TIMEOUT_SECONDS` | `30` | Timeout for one GitHub or Gitea API request; minimum 5 |
| `WIKI_ENABLED` | `true` | Detect and mirror populated GitHub wikis |
| `RELEASES_ENABLED` | `true` | Create and update native Gitea releases |
| `RELEASE_ASSETS_ENABLED` | `true` | Reconcile attachments for mirrored releases |
| `RELEASE_ASSET_MODE` | `all` | Keep assets for `all` releases or only the `latest` stable release |
| `RELEASE_ASSET_TIMEOUT_SECONDS` | `3600` | Timeout for each asset download or upload; minimum 30 |
| `PACKAGES_ENABLED` | `false` | Mirror container packages linked to owned repositories |
| `GITHUB_PACKAGES_TOKEN` | none | Classic GitHub PAT with `read:packages`; required when enabled |
| `GITHUB_CONTAINER_REGISTRY` | `ghcr.io` | Source registry hostname, optionally with a port |
| `CONTAINER_IMAGE_MODE` | `all` | Keep every image digest or only the literal `latest` digest |
| `PACKAGE_MAX_BYTES` | `0` | Conservative estimated per-image byte limit; `0` disables it |
| `PACKAGE_TRANSFER_TIMEOUT_SECONDS` | `3600` | Timeout for each registry operation; minimum 30 |
| `GIT_LFS_ENABLED` | `true` | Fetch and upload reachable LFS objects before pushing refs |
| `GIT_TIMEOUT_SECONDS` | `3600` | Timeout for each Git or Git LFS command; minimum 30 |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

## Complete example

```dotenv
GITHUB_TOKEN=replace-with-github-read-token
GITHUB_USERNAME=your-github-login
GITHUB_API_URL=https://api.github.com

GITEA_URL=https://gitea.example.com
GITEA_TOKEN=replace-with-gitea-token
GITEA_OWNED_NAMESPACE=github-backups
GITEA_STARRED_NAMESPACE=github-archive

SYNC_INTERVAL=6h
SYNC_ON_STARTUP=true
DATABASE_PATH=/data/githarbor.db
DESTINATION_PRIVATE=true
API_TIMEOUT_SECONDS=30
WIKI_ENABLED=true
RELEASES_ENABLED=true
RELEASE_ASSETS_ENABLED=true
RELEASE_ASSET_MODE=all
RELEASE_ASSET_TIMEOUT_SECONDS=3600
PACKAGES_ENABLED=false
GITHUB_PACKAGES_TOKEN=replace-with-classic-github-packages-token
GITHUB_CONTAINER_REGISTRY=ghcr.io
CONTAINER_IMAGE_MODE=all
PACKAGE_MAX_BYTES=0
PACKAGE_TRANSFER_TIMEOUT_SECONDS=3600
GIT_LFS_ENABLED=true
GIT_TIMEOUT_SECONDS=3600
LOG_LEVEL=INFO
```

## Scheduling

`SYNC_INTERVAL` is the delay between complete runs. Valid examples are `900`, `30m`, `6h`, and
`1d`. The schedule is in process memory; after a restart, `SYNC_ON_STARTUP=true` performs a fresh run
and the interval begins again.

Set `SYNC_ON_STARTUP=false` when the first large transfer should be started manually from the
dashboard. Scheduled runs still occur after the configured interval.

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
three default to `true`; package mirroring defaults to `false` because it needs another GitHub token
and can consume substantial registry storage.

- `WIKI_ENABLED=false` skips wiki detection and mirroring.
- `RELEASES_ENABLED=false` skips release metadata and release assets.
- `RELEASE_ASSETS_ENABLED=false` keeps release metadata current but skips attachment reconciliation.
- `PACKAGES_ENABLED=false` skips container discovery and transfer.

Disabling a layer does not remove data already preserved in Gitea. If releases are disabled, the
release-assets setting and asset mode have no effect.

`RELEASE_ASSET_MODE=all` keeps assets on every visible release. Set it to `latest` to keep assets
only on GitHub's latest published stable release while continuing to mirror metadata for every
visible release. GitHub excludes drafts and prereleases from “latest.” When GitHub selects a new
latest release, GitHarbor uploads its assets and safely deletes assets it previously managed from
older releases. If the new latest asset set has any failure, older assets remain until a later retry
succeeds. Unmanaged or externally changed Gitea attachments are retained with a warning.

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

## Timeouts

`API_TIMEOUT_SECONDS` covers individual HTTP API calls. GitHarbor retries transient API and rate
limit failures independently.

`RELEASE_ASSET_TIMEOUT_SECONDS` applies separately to each GitHub release-asset download and Gitea
upload. Assets are processed one at a time, so temporary space needs to fit only the current asset.
Increasing this timeout does not change Gitea's attachment limit or a reverse proxy's request-body
limit.

`PACKAGE_TRANSFER_TIMEOUT_SECONDS` applies to each Skopeo inspect, copy, tag-list, or delete
operation. Large multi-platform images may need more time. Registry layers stream directly between
the source and destination through the GitHarbor container; ensure its network path can sustain the
transfer.

`GIT_TIMEOUT_SECONDS` applies to each clone, LFS transfer, and push command. Increase it when large
repositories fail at a repeatable duration. Also confirm that the container host has enough temporary
space for one bare repository and its reachable LFS objects.

## Database and Compose volume

The supplied `docker-compose.yml` pins `DATABASE_PATH=/data/githarbor.db` and mounts the named volume
`githarbor-data` at `/data`. That Compose `environment` value takes precedence over the same key in
`.env`.

To use a bind mount or different container path, update both the `environment` and `volumes` entries
in `docker-compose.yml`. Ensure container UID `10001` can write the target. Never run two GitHarbor
replicas against the same SQLite file.

## Network exposure

The supplied port mapping is `127.0.0.1:9005:8000`, so only the Docker host can reach the dashboard.
Do not change it to a public bind unless an authenticated reverse proxy or equivalent access control
protects the service. GitHarbor has no built-in login.
