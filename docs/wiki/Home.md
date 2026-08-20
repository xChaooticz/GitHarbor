# GitHarbor documentation

GitHarbor discovers repositories owned and starred by one GitHub account and preserves their Git
history and reachable Git LFS objects in Gitea. It never automatically deletes a destination
repository when the source disappears or a star is removed.

## Start here

1. Follow [Getting started](https://github.com/xChaooticz/GitHarbor/wiki/Getting-Started) from the
   prerequisites through the first successful synchronization.
2. Use [Tokens and permissions](https://github.com/xChaooticz/GitHarbor/wiki/Tokens-and-Permissions)
   to create least-privilege GitHub and Gitea credentials.
3. Use [Gitea organizations](https://github.com/xChaooticz/GitHarbor/wiki/Gitea-Organizations) to
   prepare the two destination namespaces.
4. Review [Configuration](https://github.com/xChaooticz/GitHarbor/wiki/Configuration) before changing
   the schedule, visibility, database path, timeouts, or LFS behavior.
5. Keep [Operations](https://github.com/xChaooticz/GitHarbor/wiki/Operations) and
   [Troubleshooting](https://github.com/xChaooticz/GitHarbor/wiki/Troubleshooting) nearby after
   deployment.

## What is preserved

- Branches, tags, commit history, notes, and other Git refs
- Git LFS objects reachable from mirrored refs when LFS support is enabled
- A stable mapping based on GitHub's numeric repository ID, even after a rename or transfer
- The last known Gitea copy when a repository becomes inaccessible or is unstarred

GitHarbor does not migrate issues, pull requests, Actions, releases, wikis, discussions, release
assets, LFS locks, or LFS objects that are no longer reachable from a Git ref.

## Important safety rules

- Run one GitHarbor container per database.
- Do not expose the dashboard directly to an untrusted network; it has no built-in login in v0.1.
- Do not manually create repositories at paths GitHarbor will use. GitHarbor creates its own
  management marker and refuses to overwrite an unmarked or mismatched repository.
- Back up Gitea itself as well as GitHarbor's SQLite volume. Gitea contains the preserved repository
  data; GitHarbor's database contains inventory and run history.

The project source, changelog, and issue tracker are in the
[GitHarbor repository](https://github.com/xChaooticz/GitHarbor).
