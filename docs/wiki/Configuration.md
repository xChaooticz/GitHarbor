# Configuration

GitHarbor reads environment variables at process startup. Docker Compose loads them from `.env`.
After changing a value, recreate the container rather than using a simple restart:

```sh
docker compose up -d --force-recreate githarbor
```

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

The primary Git mirror is always enabled. `WIKI_ENABLED`, `RELEASES_ENABLED`, and
`RELEASE_ASSETS_ENABLED` control the optional layers independently. All default to `true`.

- `WIKI_ENABLED=false` skips wiki detection and mirroring.
- `RELEASES_ENABLED=false` skips release metadata and release assets.
- `RELEASE_ASSETS_ENABLED=false` keeps release metadata current but skips attachment reconciliation.

Disabling a layer does not remove data already preserved in Gitea. If releases are disabled, the
release-assets setting and asset mode have no effect.

`RELEASE_ASSET_MODE=all` keeps assets on every visible release. Set it to `latest` to keep assets
only on GitHub's latest published stable release while continuing to mirror metadata for every
visible release. GitHub excludes drafts and prereleases from “latest.” When GitHub selects a new
latest release, GitHarbor uploads its assets and safely deletes assets it previously managed from
older releases. If the new latest asset set has any failure, older assets remain until a later retry
succeeds. Unmanaged or externally changed Gitea attachments are retained with a warning.

## Timeouts

`API_TIMEOUT_SECONDS` covers individual HTTP API calls. GitHarbor retries transient API and rate
limit failures independently.

`RELEASE_ASSET_TIMEOUT_SECONDS` applies separately to each GitHub release-asset download and Gitea
upload. Assets are processed one at a time, so temporary space needs to fit only the current asset.
Increasing this timeout does not change Gitea's attachment limit or a reverse proxy's request-body
limit.

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

The supplied port mapping is `127.0.0.1:8000:8000`, so only the Docker host can reach the dashboard.
Do not change it to a public bind unless an authenticated reverse proxy or equivalent access control
protects the service. GitHarbor has no built-in login.
