# Gitea organizations

GitHarbor separates repositories you own from repositories you starred. Two Gitea organizations
make that boundary obvious and prevent naming collisions between the two sets.

Recommended names:

| Purpose | Organization | Environment variable |
|---|---|---|
| GitHub repositories you own | `github-backups` | `GITEA_OWNED_NAMESPACE` |
| GitHub repositories you starred | `github-archive` | `GITEA_STARRED_NAMESPACE` |

You may choose different names. Enter only the organization slug in `.env`, not a URL.

## Create the dedicated account

Using a separate Gitea user such as `githarbor` limits the impact of its write-capable token. Create
the user through normal Gitea registration or ask the instance administrator to create it. The
account does not need site-administrator privileges.

Sign in as this account for the remaining steps.

## Create the owned-repository organization

1. Use the **+** menu in Gitea's top navigation and choose **New Organization**. You can also open
   `/org/create` on your Gitea instance.
2. Set **Organization Name** to `github-backups`.
3. Choose the visibility appropriate for your installation. **Private** is a safe default when
   preserved repositories may contain private source code.
4. Create the organization.
5. Confirm the dedicated account belongs to the organization owners team and can create
   repositories.

## Create the starred-repository organization

Repeat the same process with the name `github-archive`. Keeping starred repositories separate helps
distinguish personal backups from third-party archival copies and preserves recognizable names.

Gitea also exposes an official
[organization creation API](https://docs.gitea.com/api/operations/org-create/), but manual creation is
normally simpler and keeps organization creation outside GitHarbor's token permissions.

## Create the Gitea token

Once both organizations exist, follow
[Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions). For
organization destinations, select `read:user`, `write:organization`, and `write:repository` only.

## Configure GitHarbor

Set the matching values in `.env`:

```dotenv
GITEA_OWNED_NAMESPACE=github-backups
GITEA_STARRED_NAMESPACE=github-archive
```

The namespaces may be the same organization, but two organizations make ownership and retention
intent easier to audit.

## Do not pre-create destination repositories

GitHarbor creates each repository with a management marker in its Gitea description. Before every
push, it requires the marker to match the stable GitHub repository ID and repository kind. This
prevents an accidental mirror push from overwriting an unrelated repository.

If you manually create a repository at a path GitHarbor wants, synchronization stops with a marker
error. Rename or remove the unrelated empty repository through Gitea after confirming its contents;
do not copy or forge GitHarbor's marker.

## Repository names

- Owned `github-user/my-project` becomes `github-backups/my-project`.
- Starred `some-owner/tool` normally becomes `github-archive/some-owner--tool`.

GitHarbor adds the stable-ID suffix `--gh123456` only when normalized names or an existing Gitea
path collide. On upgrade, an old always-suffixed repository is renamed automatically when its
management marker matches and the clean path is free. Collision names and destinations whose source
was renamed or transferred remain unchanged.

## LFS prerequisite

When `GIT_LFS_ENABLED=true`, the Gitea administrator must enable the built-in LFS server. Add this to
Gitea's `app.ini` and restart Gitea:

```ini
[server]
LFS_START_SERVER = true
```

The storage path can be customized under `[lfs]`; see the official
[Gitea Git LFS setup](https://docs.gitea.com/1.26/administration/git-lfs-setup/). Include that LFS
storage in Gitea's own backup plan.
