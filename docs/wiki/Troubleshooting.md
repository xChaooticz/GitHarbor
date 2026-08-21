# Troubleshooting

Start with the container status and recent logs:

```sh
docker compose ps
docker compose logs --tail 200 githarbor
```

Then open the dashboard and the affected repository's detail page. GitHarbor records the last error
or release-asset warning per repository and keeps global run history in SQLite.

## Container exits during startup

Common causes are a missing required variable, an invalid URL, or an unwritable database path.

```sh
docker compose config
docker compose logs githarbor
```

`docker compose config` validates Compose interpolation but can display resolved non-secret settings;
do not share its output publicly without reviewing it. Confirm `.env` exists beside
`docker-compose.yml`. The supplied volume path must be writable by container UID `10001`.

## Gitea or GitHub connection fails

- Confirm the provider URL works from the Docker host.
- Remember that `localhost` inside a container points to that container. For Gitea on Docker Desktop,
  try `host.docker.internal`; for Compose services on a shared network, use the service name.
- Confirm DNS, firewall, proxy, and TLS certificate trust from the container's network.
- For a private certificate authority, add its CA certificate to a derived image or trusted runtime
  configuration. Do not disable TLS verification or send tokens over untrusted plain HTTP.
- Recreate the container after editing `.env`; a restart alone retains the old environment.

## `GITHUB_USERNAME` mismatch

GitHarbor calls the authenticated-user endpoint and requires its login to match `GITHUB_USERNAME`.
This prevents a public listing from silently appearing successful while private owned repositories
are omitted.

Use the login that created the token. Do not use a display name, email address, or organization name.

## Private GitHub repositories are missing

For a fine-grained token:

- Select the missing repository, or choose **All repositories** for the token's resource owner.
- Grant repository **Contents: Read-only**; Metadata read is included automatically.
- Approve or authorize the token if an organization or enterprise policy requires it.

For a classic token, private cloning needs the `repo` scope. See
[Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions).

GitHarbor's owned set intentionally uses the `owner` affiliation. Repositories available only through
organization membership or collaborator access are not classified as personally owned.

## Starred repositories are missing

Grant account **Starring: Read-only** to a fine-grained GitHub token. Make sure
`GITHUB_USERNAME` is the account whose stars should be listed.

If a previously known star disappears, GitHarbor marks it `unstarred` and preserves its Gitea copy.
Starring it again reactivates the same record on the next successful discovery.

## Gitea returns `401` or `403`

For organization destinations, the token needs:

- `read:user`
- `write:organization`
- `write:repository`

The token account must also be an owner of both destination organizations, or otherwise have normal
Gitea permission to create and push repositories. For a personal-user destination, use `write:user`
instead of `read:user`.

Tokens from older Gitea versions may not have granular scope controls. Use a dedicated account and
upgrade Gitea when practical.

## Destination namespace is rejected

Each namespace must be either:

- An organization visible and writable to the Gitea token account, or
- The authenticated Gitea user's own username.

Use only the namespace slug in `.env`. Do not use a complete URL or `owner/repository` path. Create
the organizations first using the
[Gitea organizations guide](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations).

## Destination marker refusal

GitHarbor found a repository at the desired Gitea path but cannot prove that it owns the mirror. This
is an intentional safety stop, not a transient error.

Inspect the repository before changing anything. If it is unrelated, rename it. If it is an empty
repository created manually for GitHarbor, remove it through Gitea after confirming it contains
nothing valuable and let GitHarbor create the destination. Never forge or copy management markers.

## Git LFS upload or download fails

Check all of the following:

1. Gitea has `LFS_START_SERVER = true` and was restarted after the change.
2. `GIT_LFS_ENABLED=true` in the active container environment.
3. The GitHub token can read the source repository contents and LFS objects.
4. The Gitea token can write the destination repository.
5. Gitea's LFS storage and the Docker host's temporary storage have free space.
6. Reverse proxies allow the LFS request size and duration.

GitHarbor uploads LFS objects before publishing Git refs. On failure, it intentionally retains the
previous destination refs rather than publishing pointers whose objects are missing.

After correction, retry the repository and validate by cloning from Gitea, then running:

```sh
git lfs pull
git lfs fsck
```

## Release asset is skipped or the run is `partial`

Open the repository detail page and read **Last warning**. GitHarbor keeps the repository `active`
and marks its run `partial` when Git, LFS, wiki, and release metadata succeeded but an individual
release asset could not be preserved.

Check all of the following:

1. Release attachments are enabled in Gitea.
2. The file is no larger than Gitea's configured per-attachment maximum.
3. A reverse proxy does not enforce a smaller request-body limit than Gitea.
4. Gitea accepts the asset's file type and has free storage.
5. The Gitea token can write the destination repository.
6. `RELEASE_ASSET_TIMEOUT_SECONDS` is long enough for both the download and upload.

GitHarbor queries Gitea's attachment-settings API and skips files that exceed its advertised limit
before downloading them. That API cannot reveal a stricter reverse-proxy limit, so GitHarbor also
catches an actual HTTP `413` or other upload rejection, records the warning, continues with the next
asset, and retries the skipped asset on a later sync.

## Container package is missing or skipped

Container packages are opt-in and currently limited to packages explicitly linked to repositories
owned by `GITHUB_USERNAME`. Packages linked only to starred repositories are intentionally ignored.

Check all of the following:

1. `PACKAGES_ENABLED=true` and the container was recreated after changing `.env`.
2. `GITHUB_PACKAGES_TOKEN` is a classic PAT with `read:packages`, has any required SSO approval, and
   can download the package from `GITHUB_CONTAINER_REGISTRY`.
3. The GitHub package page shows a repository connection to the owned source repository.
4. The Gitea token has `write:package`, plus its existing repository and namespace permissions.
5. The GitHarbor container can reach both registries and trusts their TLS certificates.
6. Gitea package storage, its container size limit, and any reverse proxy allow the image.
7. `PACKAGE_TRANSFER_TIMEOUT_SECONDS` is long enough for the complete multi-platform image.

In `latest` mode, GitHarbor requires a literal `latest` tag. A warning about no literal tag means it
deliberately refused to guess from timestamps; use `all` or publish `latest` upstream. Gitea does not
advertise its container-package size limit through a standard API. `PACKAGE_MAX_BYTES` can skip an
estimated oversized image early, but an actual Gitea or proxy rejection remains possible and is
reported in **Last warning**. A failed new-latest transfer keeps the previously managed image.

## Large repository times out

Increase `GIT_TIMEOUT_SECONDS`, then recreate the container. Temporary storage must fit one complete
bare clone plus its reachable LFS objects. API timeouts are controlled separately by
`API_TIMEOUT_SECONDS`.

## Repository is `unavailable` or `unstarred`

These are preserved states:

- `unavailable`: an owned repository was absent from a complete successful discovery.
- `unstarred`: a starred repository was absent from a complete successful star listing.

GitHarbor does not delete the Gitea repository. Restore GitHub access or the star and run discovery
again; the stable numeric GitHub ID reconnects the record.

## A scheduled run did not happen

- Confirm the container stayed running and healthy.
- Check that `SYNC_INTERVAL` is valid, such as `30m`, `6h`, or `1d`.
- Remember that the interval resets after process restart.
- Check for a still-running large synchronization; overlapping global runs are rejected.

## Ask for help safely

Open an issue in the [GitHarbor issue tracker](https://github.com/xChaooticz/GitHarbor/issues) with:

- GitHarbor release, Docker version, Gitea version, and host operating system
- The failing stage and redacted error
- Whether the repository is public/private and whether it uses LFS
- Relevant logs after manually removing tokens, credentials, private URLs, and repository names

Never attach `.env` or paste a full token. Revoke any credential that was exposed.
