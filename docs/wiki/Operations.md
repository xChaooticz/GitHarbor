# Operations

This page covers the routine work after GitHarbor is running: monitoring, manual synchronization,
backups, upgrades, token rotation, and recovery checks.

## Daily checks

```sh
docker compose ps
docker compose logs --tail 100 githarbor
curl --fail http://127.0.0.1:9005/api/health
```

The dashboard shows the last global run, connection state, repository status counts, and individual
run history. Investigate repositories in `error`; `unavailable` and `unstarred` are preservation
states and do not mean that the Gitea copy was deleted. A `partial` run with **Last warning** means
the core mirror succeeded but one or more release assets were safely skipped.

Logs are structured JSON and redact known token patterns. Still protect logs as operational data:
repository names and failure details may be sensitive.

## Manual synchronization

Use **Sync all repositories** in the dashboard, or call:

```sh
curl --fail -X POST http://127.0.0.1:9005/api/sync
```

An accepted request returns HTTP `202`. A concurrent global or per-repository run returns `409`
instead of starting duplicate work. A failed repository can also be retried from its detail page.

These mutation endpoints have no built-in authentication. The default LAN binding is appropriate
only for a trusted private network. Use `GITHARBOR_BIND_ADDRESS=127.0.0.1` and an authenticated proxy
for access across untrusted networks.

## Backups

There are two independent things to protect:

1. **Gitea** contains the preserved Git repositories and Git LFS objects. This is the essential
   backup target.
2. **GitHarbor's `githarbor-data` volume** contains SQLite inventory, destination mappings, status,
   and run history.

Follow Gitea's official
[Backup and Restore guide](https://docs.gitea.com/1.26/administration/backup-and-restore/) and include
its database, repositories, configuration, and LFS storage. Gitea recommends stopping the instance
for a consistent full backup.

For a consistent GitHarbor volume archive, stop the service first. On Linux or macOS:

```sh
docker compose stop githarbor
docker run --rm --volumes-from githarbor -v "$(pwd):/backup" alpine \
  tar -czf /backup/githarbor-data.tar.gz -C /data .
docker compose start githarbor
```

On PowerShell:

```powershell
docker compose stop githarbor
docker run --rm --volumes-from githarbor -v "${PWD}:/backup" alpine `
  tar -czf /backup/githarbor-data.tar.gz -C /data .
docker compose start githarbor
```

Docker's [volume backup documentation](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes)
explains the same `--volumes-from` pattern. Store archives away from the Docker host and test a
restore periodically.

The database can be recreated by rediscovery if it is lost, but existing Gitea destinations will
then lack trusted local mappings and may be rejected by the marker safety check. Preserve the volume
rather than relying on reconstruction.

## Upgrade GitHarbor

Use **Watch → Custom → Releases** on the
[GitHub repository](https://github.com/xChaooticz/GitHarbor) to receive new-release notifications.
Compare the latest release with the installed version returned by the health endpoint:

```sh
curl --fail http://127.0.0.1:9005/api/health
```

Read the [changelog](https://github.com/xChaooticz/GitHarbor/blob/main/CHANGELOG.md), then back up
GitHarbor's volume and Gitea. If `GITHARBOR_IMAGE_TAG` is pinned in `.env`, change it to the new tag,
for example `v0.6.2`. If it is `latest`, leave it unchanged; `docker compose pull` is still required
because a running container does not update itself. Pull, recreate, and verify:

```sh
docker compose pull githarbor
docker compose up -d --no-deps --wait --wait-timeout 180 githarbor
docker compose logs --tail 100 githarbor
curl --fail http://127.0.0.1:9005/api/health
```

To build a release from source instead:

```sh
git fetch --tags
git checkout v0.6.2
docker compose up -d --build --no-deps --wait --wait-timeout 180 githarbor
```

The volume remains attached when Compose replaces the container. GitHarbor applies Alembic database
migrations automatically at startup. Do not downgrade across a database migration unless the
release notes explicitly document a safe downgrade path.

## Verify a NAS installation

Run these commands on the NAS after the first installation and after every upgrade:

```sh
docker compose pull githarbor
docker compose up -d --wait --wait-timeout 180
docker compose ps
curl --fail http://127.0.0.1:9005/api/health
docker inspect --format '{{.Architecture}}' githarbor
```

The service should be `healthy`, the health endpoint should report `status` as `ok`, and the final
command should show the NAS architecture, normally `amd64` or `arm64`. GitHarbor's published image
contains both architectures. If startup fails, collect `docker compose logs --tail 200 githarbor`
before recreating the container. These checks exercise the published image on the actual NAS;
GitHarbor's CI performs the same Compose startup and health test on an isolated Linux runner.

## Rotate tokens

1. Create a replacement with the permissions in
   [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions).
2. Update only the relevant value in `.env`.
3. When replacing `GITHUB_TOKEN` and Docker must pull a private GitHarbor package, refresh the
   Docker host's saved registry login with the same new token:

   ```sh
   docker login ghcr.io -u YOUR_GITHUB_USERNAME
   ```

4. Recreate and verify the container:

   ```sh
   docker compose up -d --force-recreate githarbor
   docker compose logs --tail 100 githarbor
   ```

5. Trigger a sync and confirm both provider connections.
6. Revoke the old token.

## Verify Git LFS preservation

Choose a known LFS repository after a successful run:

```sh
git clone https://gitea.example.com/github-backups/example.git lfs-restore-test
cd lfs-restore-test
git lfs pull
git lfs fsck
```

If LFS data exists only on a non-default branch, also check that branch. GitHarbor transfers objects
reachable from all mirrored refs, not only the default branch.

## Verify wiki preservation

For a GitHub repository with at least one wiki page, open the **Wiki** tab on its managed Gitea
destination. The page content and revision history should be present. You can also verify the Git
history directly:

```sh
git clone https://gitea.example.com/github-backups/example.wiki.git wiki-restore-test
git -C wiki-restore-test log --oneline --all
```

Repositories with the GitHub wiki feature disabled, or enabled without any pages, are intentionally
skipped. A populated wiki that cannot be cloned or pushed makes the repository sync fail visibly.

## Verify release preservation

Open the **Releases** page of a managed Gitea repository and compare its tag, title, body,
draft/prerelease state, and downloadable assets with GitHub. GitHarbor processes assets one at a
time and validates their byte count; when GitHub supplies a SHA-256 digest, it validates that too.

If an asset cannot be transferred, the repository remains `active`, its run is `partial`, and the
repository detail page shows **Last warning**. Correct the Gitea attachment setting, reverse-proxy
body-size limit, storage capacity, permissions, or timeout, then retry the repository. Later syncs
retry skipped assets automatically.

With `RELEASE_ASSET_MODE=latest`, verify that only GitHub's latest published stable release has
managed attachments. When a newer stable release becomes latest, its assets are uploaded and the
previous latest release's safely identified managed assets are removed. Draft and prerelease
metadata is still mirrored, but their assets are not retained in this mode. If the new latest asset
set is incomplete or fails, the older managed assets remain as a fallback until a retry succeeds.

## Disaster recovery order

1. Restore Gitea, including its database, repositories, configuration, and LFS storage.
2. Restore the GitHarbor data volume to `/data` with UID `10001` able to read and write it.
3. Restore `.env` from a secure secret backup, or create replacement tokens.
4. Start GitHarbor and inspect startup migrations and connection checks.
5. Trigger a global sync.
6. Clone a normal repository and an LFS repository from Gitea to validate real recovery.

Do not use a successful dashboard health response as the only restore test; it proves that the app
is alive, not that Git and LFS objects can be cloned from Gitea.
