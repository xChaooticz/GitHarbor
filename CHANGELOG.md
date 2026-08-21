# Changelog

All notable changes to GitHarbor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/xChaooticz/GitHarbor/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xChaooticz/GitHarbor/releases/tag/v0.1.0
