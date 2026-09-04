# Tokens and permissions

GitHarbor uses one token per provider. `GITHUB_TOKEN` is the only GitHub credential and remains
read-only from GitHarbor's perspective. The Gitea token must create and update destinations, so it
intentionally has write access within the destination account.

Treat both tokens as passwords. Never paste them into an issue, commit, screenshot, container image,
or shell command that will remain in history.

## GitHub classic PAT

GitHarbor uses one classic PAT for every GitHub operation. Configure it as `GITHUB_TOKEN`; there is
no separate repository, star, release, LFS, or container-package token.

Create it once:

1. In GitHub, open your profile menu and choose **Settings**.
2. Open **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
3. Choose **Generate new token (classic)**, use a name such as `GitHarbor`, and set an expiration.
4. Select `repo`.
5. Select `read:packages`.
6. Leave every other scope unselected.
7. Authorize the token for organization SSO when a repository or package requires it.
8. Create the token, copy it immediately, and put it in `.env` as `GITHUB_TOKEN`.

The two scopes cover the complete setup:

| Classic scope | GitHarbor use |
|---|---|
| `repo` | Discover and clone private repositories; read metadata, Git/LFS, releases, and assets |
| `read:packages` | Discover and pull container packages, including a private GHCR deployment image |

Classic PATs do not provide separate Metadata read, Contents read, or Starring read switches.
Authenticated star listing needs no additional classic scope, and `repo` makes private repository
metadata and contents visible. GitHarbor does not need `read:user`, `write:packages`, or
`delete:packages`.

GitHarbor performs only reads on GitHub. However, the classic `repo` scope itself grants broader
repository capability than GitHarbor uses, so protect and rotate the PAT according to its full
scope. The token account must have access to every private repository and package being preserved.
A GitHub `404` can mean missing access rather than deletion; GitHarbor preserves the destination in
either case.

GitHub documents classic PAT creation in
[Managing personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
and the `read:packages` requirement in its
[container-registry guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

### GitHub Enterprise Server

Set `GITHUB_API_URL` to the enterprise REST API root, commonly
`https://github.example.com/api/v3`. Token types and available permissions vary by GHES version, so
use that server's matching GitHub documentation. The token still needs API discovery plus HTTPS Git
and LFS read access, plus authenticated package reads when package mirroring is enabled.

## Docker login for a private deployment image

Docker must authenticate before it can pull a private GitHarbor image, before the application can
read `.env`. Log in once on the Docker host, as the same operating-system user that runs Compose:

```sh
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

Paste the same classic PAT stored as `GITHUB_TOKEN` when prompted for the password. The token needs
`read:packages` and access to that deployment package. Docker stores the credential in its own
credential store; do not put the token directly on the command line. Public packages need no login.
GitHub repository visibility and container-package visibility can be configured independently.

## Gitea token

Use a dedicated Gitea account so the token cannot modify unrelated repositories. Add that account
as an owner of the two GitHarbor organizations before creating the token.

Gitea's official [API usage guide](https://docs.gitea.com/1.26/development/api-usage/) documents the
token screen, one-time token display, and granular scopes.

### Organization destinations: recommended

1. Sign in to Gitea as the dedicated account.
2. Open the profile menu → **Settings** → **Applications**.
3. In **Manage Access Tokens**, choose **Generate New Token**.
4. Name it `GitHarbor`.
5. Open **Select permissions** and set:
   - **repository**: **Read and Write** (`write:repository`)
   - **organization**: **Read and Write** (`write:organization`)
   - **user**: **Read** (`read:user`)
6. If `PACKAGES_ENABLED=true`, additionally set **package** to **Read and Write**
   (`write:package`). Otherwise leave package access disabled.
7. Leave admin, issue, notification, and other unused categories disabled.
8. Generate the token and copy it immediately. Gitea does not show the full value again.

These permissions cover exactly what the organization configuration uses:

| Permission | GitHarbor use |
|---|---|
| `read:user` | Verify the token account through `/api/v1/user` |
| `write:organization` | Inspect the organizations and create repositories inside them |
| `write:repository` | Push Git/LFS data and create releases and release attachments |
| `write:package` | Push, link, inspect, and safely remove managed container versions when enabled |

In Gitea, a write scope includes read access to the same category. The token's scopes do not replace
normal Gitea membership: the account must still have permission to create and push repositories in
each organization.

### Personal-user destination

GitHarbor also accepts the authenticated Gitea username as a namespace. If either destination uses
that personal namespace, grant **user: Read and Write** (`write:user`) instead of `read:user`, plus
`write:repository` and, when package mirroring is enabled, `write:package`. Repository creation
under `/api/v1/user/repos` is a user write operation.

### Older Gitea versions

Older Gitea releases may not expose granular token permissions. In that case, use a dedicated
low-privilege account whose only valuable access is to the GitHarbor destinations. Upgrade Gitea when
practical rather than giving a daily-use administrator account to GitHarbor.

## Store and rotate tokens

- Put token values only in the untracked `.env` file or an equivalent container secret mechanism.
- Restrict access to the deployment directory and Docker daemon.
- Use expiration dates and record a rotation reminder.
- Revoke a token immediately if it appears in Git history, logs, chat, or an issue.

To rotate a token, create the replacement first, update `.env`, and recreate the container so the
new environment is loaded:

```sh
docker compose up -d --force-recreate githarbor
docker compose logs --tail 100 githarbor
```

After a successful connection and synchronization, revoke the old token at its provider.
