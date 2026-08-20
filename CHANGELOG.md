# Changelog

All notable changes to GitHarbor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/xChaooticz/GitHarbor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xChaooticz/GitHarbor/releases/tag/v0.1.0
