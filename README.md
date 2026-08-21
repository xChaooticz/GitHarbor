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
their Git data and populated wikis into separate Gitea namespaces. Its defining rule is
preservation: a repository that vanishes, becomes inaccessible, is transferred, or is unstarred
remains in Gitea. GitHarbor records the change in state and never automatically deletes a
destination repository.

## Features

- Complete bare Git mirrors: branches, tags, history, notes, and other refs via `git push --mirror`
- Authenticated Git LFS object preservation across all mirrored refs
- Native Gitea wiki mirrors with complete GitHub wiki history and empty-wiki detection
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
repositories into Gitea's native wiki repositories.

See [Architecture decisions](docs/architecture.md) for identity, naming, safety, and failure rules.

## Quick start with Docker Compose

New to GitHarbor? The complete [Getting started guide](docs/wiki/Getting-Started.md) explains Docker
networking, both provider tokens, Gitea organizations, Git LFS, first-run verification, and security.

```sh
cp .env.example .env
# Edit .env with tokens, usernames, namespaces, and the Gitea URL.
docker compose build
docker compose up -d
docker compose logs -f githarbor
```

Open <http://127.0.0.1:8000>. Compose binds only to loopback by default. Put GitHarbor behind an
authenticated HTTPS reverse proxy before exposing it to a LAN or the internet; v0.1 has no built-in
user authentication.

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

See [Tokens and permissions](docs/wiki/Tokens-and-Permissions.md) for click-by-click creation steps,
least-privilege selections, enterprise notes, and rotation instructions.

Organization SSO restrictions still apply. A GitHub `404` can mean deletion, lost access, or an SSO
authorization problem; GitHarbor therefore treats absence as state, never as permission to delete.

### Gitea

Create an API token for a dedicated Gitea account. It needs repository read/write access, permission
to create repositories in both configured organizations (or in its own user namespace), and Git
push access. With scoped-token Gitea versions and organization destinations, grant `read:user`,
`write:organization`, and `write:repository`. A personal-user destination needs `write:user` instead
of `read:user`. GitHarbor verifies `/api/v1/user` and accepts a destination namespace only when it is
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

## Current limitations

- One GitHub identity and one application replica per SQLite database
- No built-in login/RBAC; use an authenticated reverse proxy
- No issue, pull request, Actions, discussion, release, release-asset, LFS-lock, or
  orphaned-LFS-object migration
- In-process locks do not coordinate multiple GitHarbor containers; run one replica
- Destination repository visibility is applied only at creation

GitHarbor is licensed under the [MIT License](LICENSE).
