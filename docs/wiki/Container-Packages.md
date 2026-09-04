# Container packages

GitHarbor can mirror multi-platform OCI container images from GitHub Container Registry into
Gitea's container registry. The feature is opt-in because container images can be large and GitHub
Container Registry requires a classic PAT for authenticated pulls.

## Current scope

GitHarbor mirrors only packages that GitHub reports as explicitly linked to a repository owned by
`GITHUB_USERNAME`. A package without a repository link is skipped. Packages belonging only to
starred repositories are not downloaded; support may arrive in a future release as a separate
opt-in policy.

For a source package named `project/image`, the default mapping is:

```text
ghcr.io/<github-user>/project/image
  -> <gitea-host>/<GITEA_OWNED_NAMESPACE>/project/image
```

Gitea stores packages at owner or organization level. After a successful transfer, GitHarbor links
the Gitea package to the corresponding managed repository so it appears in that repository's
package view.

## Enable the feature

The standard classic PAT in `GITHUB_TOKEN`, with `repo` and `read:packages`, handles repository
discovery, Git/LFS, releases, and source-container reads. Add `write:package` to the dedicated Gitea
token, then configure:

```dotenv
PACKAGES_ENABLED=true
GITHUB_CONTAINER_REGISTRY=ghcr.io
CONTAINER_IMAGE_MODE=all
PACKAGE_MAX_BYTES=0
PACKAGE_TRANSFER_TIMEOUT_SECONDS=3600
```

`GITEA_URL` must be the instance root without a path prefix when packages are enabled. GitHarbor
uses that URL's hostname and port as the destination registry. Recreate the container after editing
`.env`.

## Retention modes

### `all`

Every GitHub package version is copied with every source tag. GitHarbor also adds one deterministic
tag such as `githarbor-preserved-sha256-abc...` to every digest. This makes an old digest remain
addressable if a mutable tag such as `nightly` or `latest` later moves to another digest.

This mode provides the strongest preservation and consumes the most Gitea storage. Switching away
from `all` does not broadly erase images; cleanup remains limited to records eligible under the
safe latest-mode rules.

### `latest`

GitHarbor selects the manifest digest carrying the literal `latest` tag, case-insensitively. It also
copies every version tag attached to that same digest, so a source digest tagged `latest`, `1.4`, and
`1.4.0` keeps all three tags in Gitea. It does not choose the newest timestamp when `latest` is
absent or ambiguous.

When `latest` moves, GitHarbor first copies and verifies the new multi-platform image and all of its
selected tags. Only then does it remove older Gitea tags and digests whose exact version IDs were
recorded in GitHarbor's ownership journal. If the copy fails, `latest` is missing, an ID changed, or
another tag still references the old digest, the old image is retained and the repository records a
warning.

## Size and storage behavior

Gitea's `[packages] LIMIT_SIZE_CONTAINER` setting controls the instance's container upload size. A
reverse proxy and the package owner's total size/count limits can impose additional restrictions.
Gitea does not expose the container limit through a standard public settings API that GitHarbor can
query.

`PACKAGE_MAX_BYTES` is therefore an optional local guard. Before copying, GitHarbor recursively
reads the manifest list and sums unique descriptor sizes. The result is conservative and cannot
account perfectly for destination deduplication or server policy. `0` disables the local ceiling.

If the estimate exceeds the configured ceiling, or Gitea/proxy/storage rejects the real transfer,
GitHarbor skips that image, writes the reason to **Last warning**, marks the repository run
`partial`, and retries later. In latest mode it does not clean up the previous successful image.

## Safety and limitations

- Skopeo streams registry-to-registry with `--all` and `--preserve-digests` for multi-platform
  fidelity.
- Registry passwords live in permission-restricted temporary auth files, not process arguments.
- Existing same-name Gitea packages without GitHarbor ownership records are not overwritten.
- Externally changed and unmanaged tags are retained rather than deleted.
- GitHub/Gitea package ACLs, download counts, creation timestamps, signatures, attestations, and
  other non-OCI metadata are not recreated.
- Only the configured GitHub container registry is supported; other GitHub package types are not
  mirrored.

## Verify a mirror

After a successful sync, authenticate to either private registry before inspecting it. For GHCR,
use the same classic PAT stored as `GITHUB_TOKEN`:

```sh
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

For a private Gitea registry, log in with the Gitea account and paste its token at the password
prompt:

```sh
docker login GITEA_HOST -u YOUR_GITEA_USERNAME
```

Then inspect or pull the images:

```sh
docker buildx imagetools inspect ghcr.io/GITHUB_USER/PACKAGE:latest
docker buildx imagetools inspect GITEA_HOST/GITEA_NAMESPACE/PACKAGE:latest
docker pull GITEA_HOST/GITEA_NAMESPACE/PACKAGE:latest
```

The top-level digest and platform manifests should match. Run each `docker login` as the same
operating-system user that runs the corresponding Docker command. Never put tokens directly in a
command line that will remain in shell history.
