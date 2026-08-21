from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import pytest

from githarbor.services.registry import RegistryCredentials, RegistryError, SkopeoClient


class RecordingSkopeo(SkopeoClient):
    def __init__(self, responses: dict[str, bytes]) -> None:
        super().__init__()
        self.responses = responses
        self.commands: list[Sequence[str]] = []

    async def _execute(self, command: Sequence[str]) -> tuple[int, bytes, bytes]:
        self.commands.append(command)
        reference = str(command[-1]).removeprefix("docker://")
        return 0, self.responses[reference], b""


@pytest.mark.asyncio
async def test_manifest_size_estimate_recurses_and_deduplicates_layers() -> None:
    layer = f"sha256:{'b' * 64}"
    config = f"sha256:{'c' * 64}"
    child_payload: dict[str, Any] = {
        "schemaVersion": 2,
        "config": {"digest": config, "size": 10},
        "layers": [
            {"digest": layer, "size": 20},
            {"digest": layer, "size": 20},
        ],
    }
    child = json.dumps(child_payload, separators=(",", ":")).encode()
    child_digest = f"sha256:{hashlib.sha256(child).hexdigest()}"
    index_payload = {
        "schemaVersion": 2,
        "manifests": [
            {"digest": child_digest, "size": len(child)},
            {"digest": child_digest, "size": len(child)},
        ],
    }
    index = json.dumps(index_payload, separators=(",", ":")).encode()
    index_digest = f"sha256:{hashlib.sha256(index).hexdigest()}"
    repository = "ghcr.io/octocat/image"
    client = RecordingSkopeo(
        {
            f"{repository}@{index_digest}": index,
            f"{repository}@{child_digest}": child,
        }
    )

    size = await client.estimate_size(
        f"{repository}@{index_digest}",
        RegistryCredentials("ghcr.io", "octocat", "top-secret"),
    )

    assert size == len(index) + len(child) + 10 + 20
    command_text = " ".join(part for command in client.commands for part in command)
    assert "top-secret" not in command_text


@pytest.mark.asyncio
async def test_registry_access_denied_is_not_treated_as_missing() -> None:
    class DeniedSkopeo(SkopeoClient):
        async def _execute(self, _command: Sequence[str]) -> tuple[int, bytes, bytes]:
            return 1, b"", b"requested access to the resource is denied"

    with pytest.raises(RegistryError, match="requested access"):
        await DeniedSkopeo().inspect_digest(
            "gitea.test/backups/image:latest",
            RegistryCredentials("gitea.test", "user", "secret"),
        )
