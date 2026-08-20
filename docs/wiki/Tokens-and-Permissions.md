# Tokens and permissions

GitHarbor uses two different credentials. The GitHub token is read-only. The Gitea token must create
and update destination repositories, so it intentionally has write access within the destination
account.

Treat both tokens as passwords. Never paste them into an issue, commit, screenshot, container image,
or shell command that will remain in history.

## GitHub token

A fine-grained personal access token is recommended for GitHub.com. GitHub documents the creation
flow in [Managing personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

### Fine-grained token: recommended

1. In GitHub, open your profile menu and choose **Settings**.
2. Open **Developer settings** → **Personal access tokens** → **Fine-grained tokens**.
3. Choose **Generate new token**.
4. Use a descriptive name such as `GitHarbor read-only backup` and choose an expiration date.
5. Set **Resource owner** to the personal account used by `GITHUB_USERNAME`.
6. Under **Repository access**, choose:
   - **All repositories** if future private repositories should be discovered automatically, or
   - **Only select repositories** for a smaller boundary that you will maintain manually.
7. Under **Repository permissions**, set **Contents** to **Read-only**. **Metadata: Read-only** is
   automatically included by GitHub.
8. Under **Account permissions**, set **Starring** to **Read-only**.
9. Leave every other permission at **No access**, create the token, and copy it immediately.

Why these permissions are needed:

| Permission | GitHarbor use |
|---|---|
| Metadata: read | List owned repositories and read repository identity/clone metadata |
| Contents: read | Clone private Git data and download reachable Git LFS objects |
| Starring: read | List the authenticated account's starred repositories |

GitHub's endpoint documentation confirms that listing authenticated repositories needs
[Metadata read](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)
and listing stars needs
[Starring read](https://docs.github.com/en/rest/activity/starring#list-repositories-starred-by-the-authenticated-user).

If you select individual repositories, GitHarbor cannot discover a new private repository until it
is added to the token. A fine-grained token is also limited to one resource owner. GitHarbor's owned
discovery currently targets personal repositories owned by `GITHUB_USERNAME`; it does not treat
organization-membership or collaborator access as ownership.

### Classic token: compatibility option

Use a classic PAT only when a fine-grained token cannot cover your account or enterprise setup:

1. Open **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
2. Choose **Generate new token (classic)** and set an expiration.
3. Select `repo` if private repositories must be discovered and cloned.
4. For public repositories only, no broad private-repository scope is needed.

`read:user` is not required by GitHarbor: it reads only the authenticated account's public login for
the username safety check. Organization SSO or enterprise policy may still require separately
authorizing the token. A GitHub `404` can represent missing access, not only a missing repository;
GitHarbor preserves the destination in either case.

### GitHub Enterprise Server

Set `GITHUB_API_URL` to the enterprise REST API root, commonly
`https://github.example.com/api/v3`. Token types and available permissions vary by GHES version, so
use that server's matching GitHub documentation. The token still needs API discovery plus HTTPS Git
and LFS read access.

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
6. Leave admin, issue, package, notification, and other categories disabled.
7. Generate the token and copy it immediately. Gitea does not show the full value again.

These permissions cover exactly what the organization configuration uses:

| Permission | GitHarbor use |
|---|---|
| `read:user` | Verify the token account through `/api/v1/user` |
| `write:organization` | Inspect the organizations and create repositories inside them |
| `write:repository` | Inspect repositories, push mirrored Git refs, and upload LFS objects |

In Gitea, a write scope includes read access to the same category. The token's scopes do not replace
normal Gitea membership: the account must still have permission to create and push repositories in
each organization.

### Personal-user destination

GitHarbor also accepts the authenticated Gitea username as a namespace. If either destination uses
that personal namespace, grant **user: Read and Write** (`write:user`) instead of `read:user`, plus
`write:repository`. Repository creation under `/api/v1/user/repos` is a user write operation.

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
