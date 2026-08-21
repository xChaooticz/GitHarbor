# GitHarbor documentation

GitHarbor discovers repositories owned and starred by one GitHub account and preserves their Git
history, populated wikis, releases, release assets, reachable Git LFS objects, and opt-in container
packages linked to owned repositories in Gitea. It never
automatically deletes a destination repository when the source disappears or a star is removed.

## Start here

1. Follow [Getting started](https://github.com/xChaooticz/GitHarbor/wiki/Getting-Started) from the
   prerequisites through the first successful synchronization.
2. Use [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions)
   to create least-privilege GitHub and Gitea credentials.
3. Use [Gitea organizations](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations) to
   prepare the two destination namespaces.
4. Review [Configuration](https://github.com/xChaooticz/GitHarbor/wiki/Configuration) before changing
   the schedule, visibility, database path, timeouts, or LFS behavior.
5. Read [Container packages](https://github.com/xChaooticz/GitHarbor/wiki/Container-Packages) before
   enabling registry mirroring and choosing its retention mode.
6. Keep [Operations](https://github.com/xChaooticz/GitHarbor/wiki/Operations) and
   [Troubleshooting](https://github.com/xChaooticz/GitHarbor/wiki/Troubleshooting) nearby after
   deployment.

## What is preserved

- Branches, tags, commit history, notes, and other Git refs
- Git LFS objects reachable from mirrored refs when LFS support is enabled
- Complete commit history for populated GitHub wikis in Gitea's native wiki
- Native Gitea releases with tag, title, body, target, draft/prerelease state, and transferable assets
- Multi-platform container images and tags linked to owned repositories when explicitly enabled
- A stable mapping based on GitHub's numeric repository ID, even after a rename or transfer
- The last known Gitea copy when a repository becomes inaccessible or is unstarred

GitHarbor does not migrate issues, pull requests, Actions, discussions, LFS locks, or LFS objects
that are no longer reachable from a Git ref. Release authorship/timestamps, asset labels/download
counts, and deleted source releases are not reproduced.

Wiki, release, release-asset, container-package, and Git LFS mirroring can be configured
independently. Release assets can be retained for every release or only GitHub's latest published
stable release. Containers can retain all discovered image digests or only the digest with the
literal `latest` tag. Disabling an optional layer preserves the Gitea data already mirrored by that
layer.

## Important safety rules

- Run one GitHarbor container per database.
- Do not expose the dashboard directly to an untrusted network; it has no built-in login.
- Do not manually create repositories at paths GitHarbor will use. GitHarbor creates its own
  management marker and refuses to overwrite an unmarked or mismatched repository.
- Back up Gitea itself as well as GitHarbor's SQLite volume. Gitea contains the preserved repository
  data; GitHarbor's database contains inventory and run history.

The project source, changelog, and issue tracker are in the
[GitHarbor repository](https://github.com/xChaooticz/GitHarbor).
