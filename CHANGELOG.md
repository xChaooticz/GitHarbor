# Changelog

All notable changes to GitHarbor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.5] - 2026-09-04

### Changed

- The dashboard, repository list, and repository detail pages automatically refresh while a
  synchronization is active, including one final refresh when it completes.
- `INFO` logs now show application startup plus detailed global and per-repository synchronization
  progress through discovery, destination setup, Git, wiki, release, and package stages.
- The dashboard labels completed copies as **Mirrored** and shows a separate **Syncing** count that
  falls as repository transfers complete.
- Wiki mirroring now initializes Gitea's missing `.wiki.git` backing repository before pushing the
  source wiki history.
- Wiki clone, initialization, and push failures now mark the repository synchronization as an
  error instead of a partial success.

### Fixed

- Dashboard, repository-page, and API sync triggers now start their background work on the
  application event loop instead of failing with an internal server error.

## [0.6.4] - 2026-09-04

### Fixed

- Format the transient Git-push retry test so the release CI completes successfully.

## [0.6.3] - 2026-09-04

### Added

- An opt-in, separately token-protected Admin dashboard for stopping active synchronization without
  stopping the container, and for explicitly resetting every repository in one configured destination
  organization.

### Changed

- Wiki and release-metadata failures now leave a successfully mirrored primary repository active and
  mark the repository run partial with a persistent warning.

### Fixed

- Gitea API errors now include its concise validation message, making rejected release creation
  actionable without logging request bodies or credentials.
- Destination Git mirror pushes retry transient HTTP 502, 503, and 504 gateway interruptions.

## [0.6.2] - 2026-09-04

### Added

- GitHub repository descriptions are copied into Gitea and refreshed during synchronization while
  retaining readable mirror provenance and the ownership marker that guards every push.

### Changed

- The README and operations guide now provide a discoverable release-notification, version-check,
  backup, image-pull, recreation, and post-upgrade verification process.

## [0.6.1] - 2026-09-04

### Fixed

- GitHub pull-request refs are preserved under `refs/githarbor/github-pull/*` instead of being
  pushed into Gitea's reserved `refs/pull/*` namespace, preventing `hook declined` sync failures.
- The GitHub default branch is applied to the destination after each successful Git mirror, so
  repositories whose default is not `main` or `master` open on the correct branch in Gitea.

### Changed

- Starred repositories now use readable `owner--repository` destination names. The `--gh<ID>`
  suffix is retained only for actual name collisions, and safely managed legacy destinations are
  renamed automatically when the clean name is available.
- Docker Compose publishes the dashboard to the private network by default through configurable
  `GITHARBOR_BIND_ADDRESS` and `GITHARBOR_PORT` settings. Deployments should restrict access with
  their host firewall because GitHarbor does not provide dashboard authentication.

## [0.6.0] - 2026-09-04

### Changed

- Container-package discovery and GHCR pulls now use `GITHUB_TOKEN`, removing the separate
  `GITHUB_PACKAGES_TOKEN` setting. Setup now uses one classic GitHub PAT with `repo` and
  `read:packages` for repository, release, LFS, package, and private GHCR reads.

## [0.5.1] - 2026-08-21

### Added

- Automated multi-platform GHCR publishing for releases, plus a manual workflow for rebuilding a
  specific release tag.
- A complete Docker Compose startup and health-endpoint test, followed by a blocking container
  vulnerability scan on every change and a weekly schedule.
- An on-NAS installation and upgrade verification procedure in the operations guide.

### Changed

- Docker Compose now pulls `ghcr.io/xchaooticz/githarbor:latest` by default while retaining a local
  `--build` path and an optional `GITHARBOR_IMAGE_TAG` release pin.
- The default loopback dashboard port changed from `8000` to `9005`; the container continues to
  listen on internal port `8000`.
- All third-party GitHub Actions are pinned to immutable commits and kept current by Dependabot.

### Security

- The Docker build installs current Debian security upgrades, and CI rejects fixable high or
  critical operating-system and Python-package vulnerabilities.

## [0.5.0] - 2026-08-21

### Added

- Opt-in GitHub Container Registry package mirroring for packages linked to owned repositories.
- `CONTAINER_IMAGE_MODE=all|latest` retention: `all` preserves every discovered digest and tag,
  while `latest` retains the literal `latest` digest and every version tag attached to it.
- Multi-platform OCI copying with digest verification, a durable ownership journal, guarded Gitea
  package linking, and safe managed-tag cleanup.
- Configurable package transfer timeout and conservative per-image size ceiling, with persistent
  warnings for oversized or rejected transfers.

### Changed

- The Docker image now includes Skopeo for authenticated registry-to-registry image transfer.
- Gitea package or reverse-proxy size rejections leave the repository active, mark the run partial,
  retain the previous latest image, and retry during a later sync.

### Security

- Registry credentials are passed through short-lived permission-restricted auth files rather than
  process arguments, and GitHub package reads use a separate classic `read:packages` token.

## [0.4.0] - 2026-08-21

### Added

- Independent `WIKI_ENABLED`, `RELEASES_ENABLED`, and `RELEASE_ASSETS_ENABLED` switches, all enabled
  by default to preserve existing behavior.
- `RELEASE_ASSET_MODE=all|latest` retention control. `latest` uses GitHub's latest published stable
  release and removes safely managed assets from older releases when the latest release changes.

### Changed

- Disabling an optional mirror layer now skips it without deleting data already preserved in Gitea.
- Release metadata remains mirrored for all visible releases when asset retention is set to `latest`.

## [0.3.0] - 2026-08-21

### Added

- Continuous GitHub release metadata and native Gitea release-asset mirroring.
- Gitea attachment-limit discovery, one-file-at-a-time streaming, exact byte-count and optional
  SHA-256 validation, and a configurable transfer timeout.
- Durable hidden ownership metadata for idempotent release updates and guarded asset reconciliation.
- Persistent per-repository warnings and partial run status for assets that are oversized, disabled,
  incomplete, rejected, or otherwise unable to transfer.

### Changed

- Release asset failures no longer hide successful Git, LFS, wiki, or release-metadata preservation;
  they remain visible and are retried on later syncs.
- Unmanaged same-tag releases and externally changed managed assets are preserved instead of being
  overwritten or deleted.

## [0.2.0] - 2026-08-21

### Added

- Continuous GitHub wiki detection and full-history mirroring into each managed Gitea repository's
  native wiki.
- Empty-wiki detection that skips repositories whose wiki feature is enabled but has no pages.
- Integration coverage proving wiki contents and commit history survive a real Git mirror.

### Changed

- Git mirroring now uses a shared internal path for primary repositories and wiki repositories while
  retaining fail-closed Git LFS behavior for the primary repository.

## [0.1.1] - 2026-08-21

### Added

- Beginner-focused installation guide covering Docker networking, Gitea preparation, first startup,
  verification, and safe exposure.
- Least-privilege GitHub and Gitea token walkthroughs with fine-grained, classic, organization, and
  personal-namespace variants.
- Gitea organization, complete configuration, operations, backup, recovery, Git LFS verification,
  and troubleshooting documentation.
- Version-controlled sources for the public GitHub Wiki, including navigation and project overview.

### Changed

- Corrected credential guidance to require Gitea `read:user`, `write:organization`, and
  `write:repository` for organization destinations while removing an unnecessary GitHub
  `read:user` recommendation.
- Expanded README navigation and setup links so new operators can reach the full guides directly.

## [0.1.0] - 2026-08-21

### Added

- GitHub discovery for owned and starred repositories with pagination, retry handling, and stable
  numeric repository identity.
- Safe Gitea destination creation with separate namespaces, collision-proof starred names, and
  ownership markers that prevent overwriting unrelated repositories.
- Complete bare Git ref mirroring with force-update and upstream-ref-deletion support.
- Authenticated Git LFS preservation for every mirrored ref, with fail-closed uploads and pinned
  HTTP endpoints.
- Preservation states for repositories that become inaccessible, transferred, deleted, or
  unstarred, without automatic destination deletion.
- SQLite inventory and run history with Alembic migrations, WAL mode, and restart recovery.
- Scheduled, startup, global, and per-repository synchronization with concurrency locks.
- FastAPI REST API and responsive Jinja dashboard for status, filtering, history, and manual syncs.
- Structured JSON logging with credential redaction and temporary askpass authentication.
- Non-root Docker image, Docker Compose deployment, and health check.
- Unit, API, reconciliation, concurrency, and real local Git LFS integration tests.
- GitHub Actions continuous integration using the reproducible Docker test stage.
- MIT license, contribution guide, architecture decisions, and GitHarbor logo.

[Unreleased]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.5...HEAD
[0.6.5]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xChaooticz/GitHarbor/releases/tag/v0.1.0
