<p align="center">
  <img src="assets/githarbor-logo.png" alt="GitHarbor logo" width="220">
</p>

<h1 align="center">GitHarbor</h1>

<p align="center"><strong>Your self-hosted safe harbor for Git repositories.</strong></p>

<p align="center">
  <a href="docs/wiki/Getting-Started.md">Documentation</a> ·
  <a href="https://github.com/xChaooticz/GitHarbor/wiki">Wiki</a> ·
  <a href="LICENSE">MIT License</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

GitHarbor continuously discovers repositories owned and starred by one GitHub account and mirrors
their Git data, populated wikis, releases, release assets, and owned-repository container packages
into separate Gitea namespaces. Its
defining rule is preservation: a repository that vanishes, becomes inaccessible, is transferred,
or is unstarred remains in Gitea. GitHarbor records the change in state and never automatically
deletes a destination repository.

## Features

- Complete bare Git mirrors: branches, tags, history, notes, and other refs via `git push --mirror`
- Authenticated Git LFS object preservation across all mirrored refs
- Native Gitea wiki mirrors with complete GitHub wiki history and empty-wiki detection
- Native Gitea release metadata and streamed release-asset mirroring with size-limit safeguards
- Opt-in multi-platform container mirroring for packages linked to owned repositories, with
  all-image or latest-image retention
- Stable GitHub repository IDs for rename/transfer detection
- Collision-proof starred naming and guarded Gitea ownership markers
- Independent, paginated owned/starred discovery with transient API retries and rate-limit reporting
- SQLite state with WAL mode and versioned Alembic migrations
- Six-hour scheduler by default, startup sync, manual global sync, and repository retry
- Global and per-repository synchronization locks
- Responsive FastAPI/Jinja dashboard, filtering, detail pages, history, and a small REST API
- Structured JSON logs with credential redaction
- Non-root, health-checked, single-container Docker deployment

## Architecture

GitHarbor is one Python process. FastAPI serves the UI/API, an asyncio scheduler invokes the
reconciliation service, HTTPX clients speak to GitHub and Gitea, and SQLAlchemy stores inventory and
run history in SQLite. Each repository operation clones a bare mirror into an isolated OS temporary
directory, transfers referenced LFS objects, pushes the refs, and removes the directory. There are no
persistent working trees. Populated GitHub wikis are mirrored separately through their Git
repositories into Gitea's native wiki repositories. Releases and their assets are reconciled after
the Git push through the GitHub and Gitea APIs. Owned-repository container packages are discovered
through the GitHub Packages API and copied registry-to-registry with Skopeo when enabled.

See [Architecture decisions](docs/architecture.md) for identity, naming, safety, and failure rules.

## Quick start with Docker Compose

New to GitHarbor? The complete [Getting started guide](docs/wiki/Getting-Started.md) explains Docker
networking, both provider tokens, Gitea organizations, Git LFS, first-run verification, and security.

```sh
cp .env.example .env
# Edit .env with tokens, usernames, namespaces, and the Gitea URL.
docker compose up -d
docker compose logs -f githarbor
```

This pulls `ghcr.io/xchaooticz/githarbor:latest`. To build the image locally from the checked-out
source instead, use:

```sh
docker compose up -d --build
```

Set `GITHARBOR_IMAGE_TAG=v0.5.1` in `.env` to pin a specific published release. Private GHCR
packages require `docker login ghcr.io` before Compose can pull them.

Open <http://127.0.0.1:9005>. Compose maps host port `9005` to the container's internal port `8000`
and binds only to loopback by default. Put GitHarbor behind an
authenticated HTTPS reverse proxy before exposing it to a LAN or the internet; GitHarbor has no
built-in user authentication.

The named volume `githarbor-data` contains SQLite state. Gitea itself stores the preserved Git data.
Back up both the Gitea installation and this volume.

## Credentials and permissions

### GitHub

`GITHUB_TOKEN` is read-only from GitHarbor's perspective and `GITHUB_USERNAME` must equal its
authenticated account. GitHarbor never writes to GitHub.

- Fine-grained PAT: select every repository that should be backed up; grant **Metadata: read**,
  **Contents: read**, and account **Starring: read**. New private repositories must also be added to
  the token's repository selection (or select all repositories).
- Classic PAT: `repo` is needed to clone private repositories. Public-only operation can use the
  smaller public read access supported by GitHub. GitHarbor does not require the `read:user` scope.

Container packages require a separate classic PAT in `GITHUB_PACKAGES_TOKEN` with
`read:packages`. GitHub's container registry does not accept a fine-grained PAT for this registry
login. Leave `PACKAGES_ENABLED=false` if package preservation is not needed.

See [Tokens and permissions](docs/wiki/Tokens-and-Permissions.md) for click-by-click creation steps,
least-privilege selections, enterprise notes, and rotation instructions.

Organization SSO restrictions still apply. A GitHub `404` can mean deletion, lost access, or an SSO
authorization problem; GitHarbor therefore treats absence as state, never as permission to delete.

### Gitea

Create an API token for a dedicated Gitea account. It needs repository read/write access, permission
to create repositories in both configured organizations (or in its own user namespace), and Git
push access. With scoped-token Gitea versions and organization destinations, grant `read:user`,
`write:organization`, and `write:repository`. Add `write:package` when container packages are
enabled. A personal-user destination needs `write:user` instead of `read:user`. GitHarbor verifies
`/api/v1/user` and accepts a destination namespace only when it is
an organization accessible to the token or the authenticated user's own namespace. The
[Gitea organizations guide](docs/wiki/Gitea-Organizations.md) covers the recommended two-organization
layout.

Tokens stay in environment memory. They are not persisted to SQLite, HTML, API output, Git config,
or command arguments. Git authentication uses a temporary askpass helper whose token comes from a
child-process environment.

## Configuration

| Variable | Required/default | Meaning |
|---|---:|---|
| `GITHUB_TOKEN` | required | Read-capable GitHub token |
| `GITHUB_USERNAME` | required | Login owning the token and owned repository set |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub API base (also supports GHES) |
| `GITEA_URL` | required | Gitea root URL, without `/api/v1` |
| `GITEA_TOKEN` | required | Gitea API/Git token |
| `GITEA_OWNED_NAMESPACE` | required | Gitea user or organization for owned repositories |
| `GITEA_STARRED_NAMESPACE` | required | Gitea user or organization for starred repositories |
| `SYNC_INTERVAL` | `6h` | Positive seconds or `s`, `m`, `h`, `d` duration |
| `SYNC_ON_STARTUP` | `true` | Run discovery and synchronization after startup |
| `DATABASE_PATH` | `/data/githarbor.db` | Persistent SQLite path |
| `DESTINATION_PRIVATE` | `true` | Create new Gitea destinations as private |
| `API_TIMEOUT_SECONDS` | `30` | Per-request API timeout |
| `WIKI_ENABLED` | `true` | Mirror populated GitHub wikis |
| `RELEASES_ENABLED` | `true` | Mirror GitHub release metadata |
| `RELEASE_ASSETS_ENABLED` | `true` | Mirror assets when release mirroring is enabled |
| `RELEASE_ASSET_MODE` | `all` | Asset retention: `all` releases or only `latest` stable release |
| `RELEASE_ASSET_TIMEOUT_SECONDS` | `3600` | Per release-asset download or upload timeout |
| `PACKAGES_ENABLED` | `false` | Mirror container packages linked to owned repositories |
| `GITHUB_PACKAGES_TOKEN` | required when enabled | Classic GitHub PAT with `read:packages` |
| `GITHUB_CONTAINER_REGISTRY` | `ghcr.io` | Source container registry host |
| `CONTAINER_IMAGE_MODE` | `all` | Container retention: every digest or the literal `latest` digest |
| `PACKAGE_MAX_BYTES` | `0` | Conservative per-image size ceiling; `0` disables it |
| `PACKAGE_TRANSFER_TIMEOUT_SECONDS` | `3600` | Per Skopeo registry operation timeout |
| `GIT_LFS_ENABLED` | `true` | Fetch and upload LFS objects before publishing Git refs |
| `GIT_TIMEOUT_SECONDS` | `3600` | Clone or push timeout per command |
| `LOG_LEVEL` | `INFO` | JSON log threshold |

Settings are validated on startup. Secrets have no defaults. Existing Gitea repositories are never
made public/private or otherwise reconfigured automatically.

## Repository organization and preservation

Owned repository `github-user/my-project` becomes
`GITEA_OWNED_NAMESPACE/my-project`. A starred repository becomes
`GITEA_STARRED_NAMESPACE/github-owner--repository--gh123456`. The owner prevents human ambiguity;
the stable GitHub numeric ID guarantees uniqueness after normalization. Once assigned, the
destination name stays fixed across upstream renames and transfers.

Every created Gitea description contains a management marker with the GitHub ID and repository kind.
Before every push, an existing destination must have the exact expected marker. A missing or
mismatched marker stops the operation rather than overwriting an unrelated repository.

Successful discovery updates records by `(github_id, kind)`. Missing owned repositories become
`unavailable`; missing starred repositories become `unstarred`. Neither transition calls a Gitea
delete API. Reappearing IDs become active and resume synchronization. An individual sync error marks
only that record `error`; the rest of a global run continues.

## Git semantics and LFS

`git clone --mirror` followed by `git push --mirror` preserves ordinary Git objects and refs,
including force pushes and upstream ref deletions. Deleting an upstream branch or tag therefore also
deletes that ref from the GitHarbor-managed destination; deleting the destination repository itself
never happens automatically.

With `GIT_LFS_ENABLED=true` (the default), GitHarbor runs authenticated `git lfs fetch --all` against
the source and `git lfs push --all` against a named destination remote before it publishes Git refs.
The HTTP LFS URLs are pinned to the API-provided clone URLs rather than accepting a repository-owned
`.lfsconfig` redirect. If any LFS step fails, the refs are not pushed and that repository is marked
`error`. Set `GIT_LFS_ENABLED=false` only when pointer-only mirroring is intentional.

Gitea must have its LFS server enabled (`LFS_START_SERVER = true`). GitHarbor preserves LFS objects
reachable from mirrored refs; LFS locks and already-orphaned server objects are outside the Git
history and are not copied.

## Wiki mirroring

GitHarbor reads GitHub's repository-level wiki capability flag on every discovery or individual
sync. Disabled wikis are skipped without another network operation. For an enabled wiki, GitHarbor
checks the separate `<repository>.wiki.git` remote for refs; an enabled but never-created wiki is
also skipped.

When pages exist, GitHarbor enables the native wiki unit on the managed Gitea destination and uses a
bare mirror push to preserve every wiki commit and ref. Like the primary Git mirror, this makes the
GitHub wiki authoritative: an upstream wiki force-update or ref deletion is reflected in Gitea.
GitHub wiki attachments committed into the wiki repository are ordinary Git objects and are
preserved. A wiki clone or push failure marks that repository synchronization as an error.
Set `WIKI_ENABLED=false` to skip wiki checks and updates. Existing Gitea wiki data is retained.

## Release and release-asset mirroring

After the Git refs (including release tags) are current, GitHarbor lists GitHub releases and creates
or updates native Gitea releases. It preserves the tag, title, Markdown body, target commitish,
draft state, and prerelease state. A hidden marker appended to the Gitea release body records the
GitHub release ID and managed asset IDs without changing the visible source text. GitHarbor refuses
to overwrite an existing same-tag Gitea release that lacks this ownership marker.

`RELEASES_ENABLED=false` skips both release metadata and assets without deleting existing Gitea
releases. With releases enabled, `RELEASE_ASSETS_ENABLED=false` continues updating release metadata
but leaves all existing attachments untouched.

Assets are downloaded and uploaded one at a time through an isolated temporary file. GitHarbor
checks the downloaded byte count and verifies GitHub's SHA-256 digest when one is available. It asks
Gitea's attachment-settings API for the advertised per-file maximum and skips an oversized asset
before downloading it. Attachments disabled by Gitea, GitHub assets that are not fully uploaded,
HTTP `413` responses, reverse-proxy or storage rejections, and individual transfer failures are also
skipped without failing the Git or release-metadata mirror.

Every skipped asset is written to the repository's persistent **Last warning**, and that repository
run is marked `partial`; the repository itself remains `active` and later syncs retry the asset. A
reverse proxy can enforce a smaller request limit than Gitea advertises, so a successful proactive
check cannot guarantee the upload will be accepted. Adjust `RELEASE_ASSET_TIMEOUT_SECONDS` for slow
large transfers.

`RELEASE_ASSET_MODE=all` mirrors assets for every visible release. `latest` still mirrors metadata
for every visible release but retains assets only on GitHub's latest published stable release, as
reported by GitHub's dedicated latest-release API. Drafts and prereleases are not candidates. When
the latest release changes, GitHarbor uploads its assets and deletes assets it previously managed
from older releases. If any new-latest asset fails, older assets are retained until a later retry
succeeds. Switching back to `all` restores eligible older assets on the next sync.

For safety, GitHarbor deletes a stale managed asset only when its recorded Gitea ID, name, and size
still match. Externally changed assets are retained with a warning, and releases absent from the
current GitHub response are retained. Gitea authorship and creation timestamps, GitHub asset labels,
and download counts cannot be recreated through the target API.

## Container package mirroring

Set `PACKAGES_ENABLED=true` to mirror GitHub Container Registry packages explicitly linked to an
owned repository. Packages associated only with starred repositories are not copied. This boundary
avoids silently archiving third-party images and may be expanded in a future release behind a
separate opt-in policy.

`CONTAINER_IMAGE_MODE=all` copies every discovered manifest and all of its tags. GitHarbor also adds
a `githarbor-preserved-sha256-...` tag per digest so a mutable source tag moving later cannot make an
older digest unreachable. `latest` means the literal case-insensitive `latest` tag—not the newest
timestamp. It copies that digest plus every other source tag attached to the same digest, such as
`1.4` and `1.4.0`. If no literal `latest` exists, GitHarbor warns and keeps the previous destination
image rather than guessing.

Multi-platform manifest lists and their referenced images are copied with preserved digests. A new
latest image is fully copied and verified before GitHarbor removes older tags and digests recorded
as its own. Unmanaged or externally changed Gitea tags are never deleted. Gitea does not advertise
its container-package size limit through a standard settings API, so `PACKAGE_MAX_BYTES` provides a
conservative preflight estimate. Gitea, storage, or reverse-proxy rejection is recorded in **Last
warning**, marks the run partial, and leaves the previous latest image intact.

See [Container packages](docs/wiki/Container-Packages.md) for setup, permissions, path mapping,
retention details, and verification commands.

## API

- `GET /api/health`
- `GET /api/status`
- `GET /api/repositories?kind=starred&status=error&search=owner`
- `GET /api/repositories/{id}`
- `POST /api/sync`
- `POST /api/repositories/{id}/sync`

Mutation endpoints return `202`; overlapping requests return `409`. OpenAPI documentation is at
`/docs`. The API never serializes tokens or clone URLs.

## Local development

```sh
python -m venv .venv
. .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn githarbor.app:app --reload
```

Verification:

```sh
pytest
ruff check .
ruff format --check .
mypy githarbor
docker build -t githarbor:local .
docker compose config
```

Tests use temporary SQLite databases and mocked clients. The suite also performs a real, token-free
Git LFS transfer between temporary local bare repositories; `git-lfs` is therefore required for
local testing and is installed in the Docker test stage.

GitHub Actions also starts the complete Compose service, checks the public health endpoint, and
blocks merges or releases when Trivy finds a fixable high or critical vulnerability in the image.
The container check runs weekly as well, so newly disclosed vulnerabilities are caught even when
the source has not changed. Third-party actions are pinned to immutable commits and updated through
Dependabot.

## Troubleshooting

See the full [Troubleshooting guide](docs/wiki/Troubleshooting.md) for provider permissions,
container networking, namespace errors, LFS failures, scheduling, and safe issue reports.

- **GitHub username mismatch:** the token account and `GITHUB_USERNAME` must match. This prevents a
  public-only endpoint from silently omitting private owned repositories.
- **Destination marker refusal:** GitHarbor found a repository at the desired Gitea path but cannot
  prove it manages that repository. Move/rename the unrelated repository; do not forge markers.
- **Repository remains unavailable/unstarred:** this is expected preservation behavior. Restore
  GitHub access or star status; the next discovery uses the stable ID and resumes.
- **Private clone fails after API discovery:** ensure the GitHub token has Contents read access to
  that repository and any required organization SSO authorization.
- **SQLite cannot open:** ensure the container's UID 10001 can write the mounted `/data` directory.
- **Large repository timeout:** raise `GIT_TIMEOUT_SECONDS`; temporary space must fit one bare clone.
- **LFS upload fails:** verify Gitea has `LFS_START_SERVER = true`, the token can write the repository,
  and its LFS storage has enough free space. Git refs are intentionally withheld on LFS failure.
- **Release asset is skipped:** inspect **Last warning** on the repository page. Compare the asset
  size with Gitea's attachment limit and any reverse-proxy body-size limit, then retry the sync.
- **Container image is skipped:** inspect **Last warning**, verify both package token scopes and
  registry reachability, then compare Gitea's package and reverse-proxy limits with
  `PACKAGE_MAX_BYTES`.

## Current limitations

- One GitHub identity and one application replica per SQLite database
- No built-in login/RBAC; use an authenticated reverse proxy
- No issue, pull request, Actions, discussion, LFS-lock, or orphaned-LFS-object migration
- Container packages are limited to packages linked to owned repositories; starred-repository
  package mirroring may be added as a separate opt-in mode in a future release
- Release authorship/timestamps, asset labels/download counts, and deleted source releases are not
  reproduced; GitHarbor preserves the last managed release instead
- In-process locks do not coordinate multiple GitHarbor containers; run one replica
- Destination repository visibility is applied only at creation

GitHarbor is licensed under the [MIT License](LICENSE).
