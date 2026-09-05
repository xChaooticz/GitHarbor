# Getting started

This guide takes a new installation from an empty directory to its first verified mirror. The
recommended base layout uses two Gitea organizations and one dedicated Gitea service account. Add a
third organization when you also want to preserve selected Forgejo or GitLab repositories.

## 1. Prerequisites

You need:

- A machine with Docker and the Docker Compose v2 plugin (`docker compose version`)
- A GitHub account whose personal repositories and stars should be preserved
- Optional Forgejo or GitLab repository URLs, plus read-only credentials when they are private
- A running Gitea instance that the GitHarbor container can reach over HTTP or HTTPS
- Enough storage for Gitea and GitHarbor's persistent bare-mirror cache

Use a supported Docker installation from the
[official Docker documentation](https://docs.docker.com/engine/install/). GitHarbor builds Git and
Git LFS into its image; you do not need to install either tool on the host.

### Container networking warning

`localhost` inside the GitHarbor container means the GitHarbor container itself. If Gitea is running
on the host:

- Docker Desktop: use a URL such as `http://host.docker.internal:3000`.
- Linux Docker Engine: use a reachable host address, or attach both services to the same Docker
  network and use the Gitea service name.

Prefer HTTPS whenever the token crosses a machine or an untrusted network.

## 2. Get GitHarbor

```sh
git clone https://github.com/xChaooticz/GitHarbor.git
cd GitHarbor
```

For a stable installation, check out the release you intend to run rather than an arbitrary commit:

```sh
git checkout v0.7.0
```

## 3. Prepare Gitea

Create a dedicated Gitea user such as `githarbor`. While signed in as that user, create these two
organizations:

- `github-backups` for repositories owned by the GitHub account
- `github-archive` for repositories starred by the GitHub account

If you will configure external repositories, also create an organization such as
`external-backups`. It is optional: each external entry can target any organization writable by the
Gitea token, or the token user's personal namespace.

The account that owns the Gitea token must be able to create repositories and push in every selected
destination organization. Follow the detailed
[Gitea organizations guide](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations).

If any source uses Git LFS, enable Gitea's LFS server before the first synchronization. The official
[Gitea LFS setup](https://docs.gitea.com/1.26/administration/git-lfs-setup/) uses:

```ini
[server]
LFS_START_SERVER = true
```

Restart Gitea after changing its configuration.

## 4. Create the tokens

Create:

- One classic GitHub PAT that GitHarbor uses only for discovery, cloning, releases, LFS, and
  container-package reads
- A Gitea token that can create repositories, push Git/LFS data, and write releases and attachments
- Optional read-only Forgejo or GitLab tokens for private external sources

Grant the classic PAT `repo` for private repository data and `read:packages` for container images.
These two scopes provide one consistent setup whether optional package mirroring is enabled now or
later. No additional GitHub scopes are needed.

The exact screens and required permissions are documented in
[Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions).

## 5. Create `.env`

On Linux or macOS:

```sh
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` in a text editor and replace the placeholders. A typical configuration is:

```dotenv
GITHARBOR_IMAGE_TAG=latest
GITHARBOR_BIND_ADDRESS=0.0.0.0
GITHARBOR_PORT=9005

GITHUB_TOKEN=github_token_goes_here
GITHUB_USERNAME=your-github-login

GITEA_URL=https://gitea.example.com
GITEA_TOKEN=gitea_token_goes_here
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

Rules that prevent common startup failures:

- `GITHUB_USERNAME` must exactly identify the account that created `GITHUB_TOKEN`.
- `GITEA_URL` is the Gitea root URL, without `/api/v1` and without a repository path.
- Namespace values are Gitea usernames or organization names, not complete URLs.
- `SYNC_CONCURRENCY` is the global repository worker limit; start with `3` and tune it to the host
  and provider limits.
- `GIT_CACHE_PATH` must be persistent. The supplied Compose file stores it in `githarbor-data`.
- Keep `.env` private. It is ignored by Git, but it is still a plaintext secret file on the host.

See [Configuration](https://github.com/xChaooticz/GitHarbor/wiki/Configuration) for every setting.

## 6. Optional: add Forgejo or GitLab sources

Skip this section when GitHub is your only source. Otherwise copy the example inventory:

```sh
cp external-sources.example.toml external-sources.toml
```

Add one `[[repositories]]` table per repository. This example lets the source API determine the ID,
name, description, and default branch, and mirrors the wiki because its URL is explicitly supplied:

```toml
version = 1

[[repositories]]
provider = "forgejo"
clone_url = "https://git.eden-emu.dev/eden-emu/eden.git"
wiki_url = "https://git.eden-emu.dev/eden-emu/eden.wiki.git"
destination_namespace = "external-backups"
```

Set `EXTERNAL_SOURCES_FILE=/config/external-sources.toml` in `.env` and uncomment the corresponding
read-only mount in `docker-compose.yml`. For a private source, configure `token_env` in TOML and add
that named variable to `.env`; never put a token in a URL or the TOML file. See
[External sources](https://github.com/xChaooticz/GitHarbor/wiki/External-Sources) for every field,
private-token setup, release behavior, and limitations.

## 7. Pull and start

```sh
docker compose up -d
docker compose ps
docker compose logs -f githarbor
```

The normal command pulls `ghcr.io/xchaooticz/githarbor:latest`. To build from the checked-out
source instead, add `--build`:

```sh
docker compose up -d --build
```

For reproducible deployments, set `GITHARBOR_IMAGE_TAG` in `.env` to a release such as `v0.7.0`.
If the GitHarbor GHCR package is private, authenticate the Docker host before running Compose:

```sh
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

Paste the same classic PAT stored as `GITHUB_TOKEN` at the password prompt. It needs
`read:packages` and access to the package. Docker stores registry authentication separately because
it must pull the image before GitHarbor can read `.env`. Run `docker login` and `docker compose` as
the same operating-system user. A private repository and a private package are not necessarily the
same thing; GitHub package visibility may be configured independently.

Press `Ctrl+C` to stop following logs; the container continues running. With
`SYNC_ON_STARTUP=true`, the first discovery starts after application startup. The first run creates
a complete bare cache for every repository and can therefore take time and bandwidth. Later runs
reuse those caches, fetch only changes, and push the resulting refs to Gitea. Up to
`SYNC_CONCURRENCY` repositories are processed at once.

## 8. Verify the installation

Open `http://DOCKER_HOST_IP:9005` from the Docker host or another device on the same private network.
You should see GitHub and Gitea connection status, repository counts, and synchronization results.
External sources appear in the inventory rather than as another global connection tile.

You can also check the health endpoint:

```sh
curl --fail http://127.0.0.1:9005/api/health
```

Then verify in Gitea:

1. Owned repositories appear in `github-backups` with their original repository name.
2. Starred repositories appear in `github-archive` with collision-resistant names such as
   `owner--repository`. A `--gh123456` suffix appears only when a real name collision requires it.
3. A repository that uses LFS can be cloned from Gitea and checked with `git lfs pull` and
   `git lfs fsck`.
4. A populated GitHub wiki appears under the managed repository's Gitea **Wiki** tab.
5. A GitHub release appears under Gitea **Releases**, with its transferable assets downloadable.
6. If packages are enabled, a container linked to an owned repository appears under the configured
   owned namespace and can be pulled from the Gitea registry.
7. If external sources are configured, each appears in its selected namespace. Only entries with a
   populated explicit `wiki_url` get a wiki, and supported native releases appear in Gitea.

The dashboard's manual **Sync all repositories** action is useful after correcting a token or
network issue. It is safe to retry; overlapping global runs are rejected.

## 9. Secure and operate it

Compose binds the dashboard to `0.0.0.0:9005`, making it available on the private LAN. GitHarbor has
no built-in authentication, so anyone who can reach the dashboard can view its operational metadata
and trigger synchronization. Do not forward this port from the router or expose it to an untrusted
network. Set `GITHARBOR_BIND_ADDRESS=127.0.0.1` for host-only or reverse-proxy access.

Next, read [Operations](https://github.com/xChaooticz/GitHarbor/wiki/Operations) for upgrades,
backups, token rotation, logs, and restore checks.
