# Operations

This page covers the routine work after GitHarbor is running: monitoring, manual synchronization,
backups, upgrades, token rotation, and recovery checks.

## Daily checks

```sh
docker compose ps
docker compose logs --tail 100 githarbor
curl --fail http://127.0.0.1:8000/api/health
```

The dashboard shows the last global run, connection state, repository status counts, and individual
run history. Investigate repositories in `error`; `unavailable` and `unstarred` are preservation
states and do not mean that the Gitea copy was deleted.

Logs are structured JSON and redact known token patterns. Still protect logs as operational data:
repository names and failure details may be sensitive.

## Manual synchronization

Use **Sync all repositories** in the dashboard, or call:

```sh
curl --fail -X POST http://127.0.0.1:8000/api/sync
```

An accepted request returns HTTP `202`. A concurrent global or per-repository run returns `409`
instead of starting duplicate work. A failed repository can also be retried from its detail page.

These mutation endpoints have no built-in authentication in v0.1. Keep the loopback port binding or
put the service behind an authenticated proxy.

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

Read the [changelog](https://github.com/xChaooticz/GitHarbor/blob/main/CHANGELOG.md) before upgrading,
then back up the volume and deploy a specific release tag:

```sh
git fetch --tags
git checkout v0.1.1
docker compose build --pull githarbor
docker compose up -d --no-deps githarbor
docker compose logs --tail 100 githarbor
```

GitHarbor applies Alembic database migrations automatically at startup. Do not downgrade across a
database migration unless the release notes explicitly document a safe downgrade path.

## Rotate tokens

1. Create a replacement with the permissions in
   [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions).
2. Update only the relevant value in `.env`.
3. Recreate and verify the container:

   ```sh
   docker compose up -d --force-recreate githarbor
   docker compose logs --tail 100 githarbor
   ```

4. Trigger a sync and confirm both provider connections.
5. Revoke the old token.

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

## Disaster recovery order

1. Restore Gitea, including its database, repositories, configuration, and LFS storage.
2. Restore the GitHarbor data volume to `/data` with UID `10001` able to read and write it.
3. Restore `.env` from a secure secret backup, or create replacement tokens.
4. Start GitHarbor and inspect startup migrations and connection checks.
5. Trigger a global sync.
6. Clone a normal repository and an LFS repository from Gitea to validate real recovery.

Do not use a successful dashboard health response as the only restore test; it proves that the app
is alive, not that Git and LFS objects can be cloned from Gitea.
