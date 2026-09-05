# Gitea organizations

GitHarbor separates repositories you own from repositories you starred. Two required Gitea
organizations make that boundary obvious and prevent naming collisions between the GitHub sets. A
third organization is recommended when external Forgejo or GitLab repositories are configured.

Recommended names:

| Purpose | Organization | Setting |
|---|---|---|
| GitHub repositories you own | `github-backups` | `GITEA_OWNED_NAMESPACE` |
| GitHub repositories you starred | `github-archive` | `GITEA_STARRED_NAMESPACE` |
| Selected Forgejo/GitLab repositories | `external-backups` | `destination_namespace` in TOML |

You may choose different names or use the same namespace for multiple sets. Enter only organization
slugs, not URLs. The first two are set in `.env`; every external source selects its namespace in the
external-sources TOML file.

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

## Optional: create the external-source organization

Repeat the process with a name such as `external-backups` when using the external-sources file. This
organization is optional: an entry can use either GitHub organization, another writable
organization, or the Gitea token user's personal namespace. A separate destination makes retention
intent and source provenance easier to audit.

The restricted dashboard bulk-reset action is deliberately limited to `GITEA_OWNED_NAMESPACE` and
`GITEA_STARRED_NAMESPACE`. External namespaces are not reset from GitHarbor's dashboard.

## Create the Gitea token

Once the required organizations exist, follow
[Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions). For
organization destinations, select `read:user`, `write:organization`, and `write:repository`. The
account must be able to create repositories in every namespace selected by either `.env` or the
external-sources file.

## Configure GitHarbor

Set the matching values in `.env`:

```dotenv
GITEA_OWNED_NAMESPACE=github-backups
GITEA_STARRED_NAMESPACE=github-archive
```

The namespaces may be the same organization, but two organizations make ownership and retention
intent easier to audit.

External destinations are configured per entry:

```toml
[[repositories]]
provider = "forgejo"
clone_url = "https://git.eden-emu.dev/eden-emu/eden.git"
destination_namespace = "external-backups"
```

## Do not pre-create destination repositories

GitHarbor copies the source repository description into Gitea and appends readable provenance plus a
management marker. Before every push, it requires the marker to match the stable provider, source
ID, and repository kind. This prevents an accidental mirror push from overwriting an unrelated
repository. Source description changes are applied during the next sync; removing the marker makes
later syncs fail closed.

If you manually create a repository at a path GitHarbor wants, synchronization stops with a marker
error. Rename or remove the unrelated empty repository through Gitea after confirming its contents;
do not copy or forge GitHarbor's marker.

## Repository names

- Owned `github-user/my-project` becomes `github-backups/my-project`.
- Starred `some-owner/tool` normally becomes `github-archive/some-owner--tool`.
- External `forgejo-owner/project` normally becomes `external-backups/project`, or the explicit
  `destination_name` when configured.

GitHarbor adds the stable-ID suffix `--gh123456` only when normalized names or an existing Gitea
path collide. On upgrade, an old always-suffixed repository is renamed automatically when its
management marker matches and the clean path is free. Collision names and destinations whose source
was renamed or transferred remain unchanged.

See [External sources](https://github.com/xChaooticz/GitHarbor/wiki/External-Sources) for the complete
TOML format and provider-specific behavior.

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
