# GitHarbor documentation

GitHarbor discovers repositories owned and starred by one GitHub account and can also read an
explicit inventory of Forgejo and GitLab repositories. It preserves Git history, configured
populated wikis, releases, supported release assets, reachable Git LFS objects, and opt-in GitHub
container packages in Gitea. It never automatically deletes a destination repository when a source
disappears, an external entry is removed, or a star is removed.

## Start here

1. Follow [Getting started](https://github.com/xChaooticz/GitHarbor/wiki/Getting-Started) from the
   prerequisites through the first successful synchronization.
2. Use [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions)
   to create the required GitHub and Gitea credentials.
3. Use [Gitea organizations](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations) to
   prepare the two GitHub namespaces and, optionally, an external-source namespace.
4. Review [Configuration](https://github.com/xChaooticz/GitHarbor/wiki/Configuration) before changing
   the schedule, visibility, database path, timeouts, or LFS behavior.
5. Read [External sources](https://github.com/xChaooticz/GitHarbor/wiki/External-Sources) to add
   individual Forgejo or GitLab repositories, including optional wikis and releases.
6. Read [Container packages](https://github.com/xChaooticz/GitHarbor/wiki/Container-Packages) before
   enabling registry mirroring and choosing its retention mode.
7. Keep [Operations](https://github.com/xChaooticz/GitHarbor/wiki/Operations) for update, backup,
   and monitoring procedures, and
   [Troubleshooting](https://github.com/xChaooticz/GitHarbor/wiki/Troubleshooting) nearby after
   deployment.

## What is preserved

- Branches, tags, commit history, notes, and other Git refs
- Git LFS objects reachable from mirrored refs when LFS support is enabled
- Complete commit history for populated GitHub wikis and explicitly configured external wikis
- Native Gitea releases with tag, title, body, target, draft/prerelease state, and transferable assets
- Multi-platform container images and tags linked to owned repositories when explicitly enabled
- A stable provider/source identity, with automatic external IDs namespaced by instance
- Source repository descriptions, with mirror provenance and a guarded ownership marker
- The last known Gitea copy when a repository becomes inaccessible, is removed from the external
  inventory, or is unstarred

GitHarbor does not migrate issues, pull requests, Actions, discussions, LFS locks, or LFS objects
that are no longer reachable from a Git ref. Release authorship/timestamps, asset labels/download
counts, and deleted source releases are not reproduced.

Wiki, release, release-asset, container-package, and Git LFS mirroring can be configured
independently. Release assets can be retained for every release or only the source provider's latest
published stable release. Forgejo attachments need a declared size; GitLab release asset links are
safely skipped when no trustworthy size is available. Containers can retain all discovered image
digests or only the digest with the literal `latest` tag. Disabling an optional layer preserves the
Gitea data already mirrored by that layer.

The first synchronization creates persistent bare-mirror caches and can take substantial time.
Provider-owned pull refs are excluded by default because large repositories can expose hundreds of
thousands of them; see [Configuration](Configuration#provider-owned-pull-refs). Later runs validate
each cache and fetch only changed Git objects before pushing changed refs to Gitea. Invalid caches
are rebuilt automatically, active caches receive automatic Git maintenance, and stale caches expire
after the configured retention period. A global run processes a configurable number of repositories
concurrently.

## Important safety rules

- Run one GitHarbor container per database.
- Do not expose the dashboard directly to an untrusted network; it has no built-in login.
- Do not manually create repositories at paths GitHarbor will use. GitHarbor creates its own
  management marker and refuses to overwrite an unmarked or mismatched repository.
- Back up Gitea itself as well as GitHarbor's data volume. Gitea contains the preserved repository
  data; GitHarbor's volume contains inventory, run history, and the reconstructable source cache.

The project source, changelog, and issue tracker are in the
[GitHarbor repository](https://github.com/xChaooticz/GitHarbor).
