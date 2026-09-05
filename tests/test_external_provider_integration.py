from __future__ import annotations

import asyncio
import base64
import os
import stat
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from githarbor.clients.gitea import GiteaClient
from githarbor.config import Settings
from githarbor.database import Database
from githarbor.models import Base, Repository, RepositoryStatus
from githarbor.services.git import GitMirror
from githarbor.services.releases import decode_release_marker
from githarbor.services.sync import SyncService

pytestmark = [
    pytest.mark.external_integration,
    pytest.mark.skipif(
        os.environ.get("GITHARBOR_EXTERNAL_INTEGRATION") != "1",
        reason="set GITHARBOR_EXTERNAL_INTEGRATION=1 with disposable provider instances",
    ),
]


class EmptyGitHub:
    async def list_owned(self) -> list[Any]:
        return []

    async def list_starred(self) -> list[Any]:
        return []


def run_git(
    *arguments: str,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def git_environment(tmp_path: Path, username: str, password: str) -> dict[str, str]:
    askpass = tmp_path / f"askpass-{uuid4().hex}.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  *Username*) printf "%s\\n" "$INTEGRATION_GIT_USERNAME" ;;\n'
        '  *) printf "%s\\n" "$INTEGRATION_GIT_PASSWORD" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return {
        **os.environ,
        "GIT_ASKPASS": str(askpass),
        "GIT_TERMINAL_PROMPT": "0",
        "INTEGRATION_GIT_USERNAME": username,
        "INTEGRATION_GIT_PASSWORD": password,
    }


async def create_token(base_url: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        response = await client.post(
            f"/api/v1/users/{username}/tokens",
            auth=(username, password),
            json={"name": f"githarbor-integration-{uuid4().hex}", "scopes": ["all"]},
        )
    assert response.status_code == 201, response.text
    token = response.json().get("sha1")
    assert isinstance(token, str) and token
    return token


async def seed_forgejo(
    base_url: str,
    username: str,
    token: str,
    repository_name: str,
    tmp_path: Path,
) -> tuple[str, str, bytes]:
    headers = {"Authorization": f"token {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        created = await client.post(
            "/api/v1/user/repos",
            json={
                "name": repository_name,
                "description": "GitHarbor external integration source",
                "private": False,
                "auto_init": False,
            },
        )
        assert created.status_code == 201, created.text
        clone_url = str(created.json()["clone_url"])

        source_work = tmp_path / "source-work"
        source_work.mkdir()
        run_git("init", "--initial-branch=main", cwd=source_work)
        run_git("config", "user.name", "GitHarbor Integration", cwd=source_work)
        run_git("config", "user.email", "integration@githarbor.invalid", cwd=source_work)
        (source_work / "README.md").write_text("# External integration source\n", encoding="utf-8")
        run_git("add", "README.md", cwd=source_work)
        run_git("commit", "-m", "Create integration source", cwd=source_work)
        run_git("tag", "v1.0.0", cwd=source_work)
        run_git("remote", "add", "origin", clone_url, cwd=source_work)
        source_environment = git_environment(tmp_path, username, token)
        run_git(
            "push",
            "--follow-tags",
            "origin",
            "main",
            cwd=source_work,
            environment=source_environment,
        )
        source_commit = run_git("rev-parse", "HEAD", cwd=source_work)

        for _attempt in range(30):
            repository = await client.get(f"/api/v1/repos/{username}/{repository_name}")
            assert repository.status_code == 200, repository.text
            if not repository.json().get("empty", True):
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("Forgejo did not observe the pushed source repository")

        wiki = await client.post(
            f"/api/v1/repos/{username}/{repository_name}/wiki/new",
            json={
                "title": "Home",
                "content_base64": base64.b64encode(b"# External wiki\n\nPreserved.\n").decode(),
                "message": "Create integration wiki",
            },
        )
        assert wiki.status_code == 201, wiki.text

        release = await client.post(
            f"/api/v1/repos/{username}/{repository_name}/releases",
            json={
                "tag_name": "v1.0.0",
                "target_commitish": "main",
                "name": "Integration release",
                "body": "Release mirrored from real Forgejo.",
                "draft": False,
                "prerelease": False,
            },
        )
        assert release.status_code == 201, release.text
        release_id = int(release.json()["id"])
        asset_contents = b"external release asset\n"
        asset = await client.post(
            f"/api/v1/repos/{username}/{repository_name}/releases/{release_id}/assets",
            params={"name": "artifact.txt"},
            files={"attachment": ("artifact.txt", asset_contents, "text/plain")},
        )
        assert asset.status_code == 201, asset.text

    return clone_url, source_commit, asset_contents


@pytest.mark.asyncio
async def test_real_forgejo_repository_wiki_and_release_mirror_to_gitea(
    tmp_path: Path,
) -> None:
    forgejo_url = os.environ["INTEGRATION_FORGEJO_URL"].rstrip("/")
    forgejo_user = os.environ["INTEGRATION_FORGEJO_USER"]
    forgejo_password = os.environ["INTEGRATION_FORGEJO_PASSWORD"]
    gitea_url = os.environ["INTEGRATION_GITEA_URL"].rstrip("/")
    gitea_user = os.environ["INTEGRATION_GITEA_USER"]
    gitea_password = os.environ["INTEGRATION_GITEA_PASSWORD"]

    source_token = await create_token(forgejo_url, forgejo_user, forgejo_password)
    destination_token = await create_token(gitea_url, gitea_user, gitea_password)
    repository_name = f"external-{uuid4().hex[:12]}"
    clone_url, source_commit, asset_contents = await seed_forgejo(
        forgejo_url, forgejo_user, source_token, repository_name, tmp_path
    )
    wiki_url = clone_url.removesuffix(".git") + ".wiki.git"

    sources_file = tmp_path / "external-sources.toml"
    sources_file.write_text(
        f'''version = 1

[[repositories]]
provider = "forgejo"
clone_url = "{clone_url}"
wiki_url = "{wiki_url}"
destination_namespace = "{gitea_user}"
''',
        encoding="utf-8",
    )

    settings = Settings(
        github_token="unused-github-token",
        github_username="unused-github-user",
        gitea_url=gitea_url,
        gitea_token=destination_token,
        gitea_owned_namespace=gitea_user,
        gitea_starred_namespace=gitea_user,
        destination_private=False,
        database_path=tmp_path / "state.db",
        git_cache_path=tmp_path / "git-cache",
        git_lfs_enabled=False,
        external_sources_file=sources_file,
    )
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    gitea = GiteaClient(
        settings.gitea_api_base,
        destination_token,
        settings.api_timeout_seconds,
        settings.release_asset_timeout_seconds,
    )
    service = SyncService(
        settings,
        database,
        EmptyGitHub(),  # type: ignore[arg-type]
        gitea,
        GitMirror(
            timeout_seconds=settings.git_timeout_seconds,
            lfs_enabled=settings.git_lfs_enabled,
            cache_path=settings.git_cache_path,
        ),
    )
    try:
        await service.sync_all("external-integration")
        await service.sync_all("external-integration-idempotence")

        with database.session_factory() as session:
            repository = session.scalar(select(Repository))
            assert repository is not None
            assert repository.status == RepositoryStatus.ACTIVE.value
            assert repository.last_error is None
            assert repository.last_warning is None
            assert repository.source_provider == "forgejo"
            assert repository.source_id

        headers = {"Authorization": f"token {destination_token}"}
        async with httpx.AsyncClient(base_url=gitea_url, headers=headers, timeout=30) as client:
            destination = await client.get(f"/api/v1/repos/{gitea_user}/{repository_name}")
            assert destination.status_code == 200, destination.text
            assert "source-provider:forgejo" in destination.json()["description"]

            releases = await client.get(f"/api/v1/repos/{gitea_user}/{repository_name}/releases")
            assert releases.status_code == 200, releases.text
            release_payloads = releases.json()
            assert len(release_payloads) == 1
            assert release_payloads[0]["tag_name"] == "v1.0.0"
            assert decode_release_marker(release_payloads[0]["body"]) is not None
            assert [item["name"] for item in release_payloads[0]["assets"]] == ["artifact.txt"]
            asset_download = await client.get(
                release_payloads[0]["assets"][0]["browser_download_url"]
            )
            assert asset_download.content == asset_contents

        destination_clone = str(destination.json()["clone_url"])
        destination_environment = git_environment(tmp_path, gitea_user, destination_token)
        assert (
            run_git(
                "ls-remote",
                destination_clone,
                "refs/heads/main",
                environment=destination_environment,
            ).split()[0]
            == source_commit
        )
        restored_wiki = tmp_path / "restored-wiki"
        run_git(
            "clone",
            destination_clone.removesuffix(".git") + ".wiki.git",
            str(restored_wiki),
            environment=destination_environment,
        )
        assert (restored_wiki / "Home.md").read_text(encoding="utf-8") == (
            "# External wiki\n\nPreserved.\n"
        )
    finally:
        await gitea.close()
