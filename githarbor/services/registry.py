from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from githarbor.services.redaction import redact

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MISSING_PATTERNS = (
    "manifest unknown",
    "name unknown",
    "not found",
)


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegistryCredentials:
    registry: str
    username: str
    password: str = field(repr=False)
    tls_verify: bool = True


class SkopeoClient:
    def __init__(self, timeout_seconds: int = 3600) -> None:
        self.timeout_seconds = timeout_seconds

    async def inspect_digest(self, reference: str, credentials: RegistryCredentials) -> str | None:
        with self._authfile(credentials) as authfile:
            command = [
                "skopeo",
                "inspect",
                "--authfile",
                str(authfile),
                "--format",
                "{{.Digest}}",
                *self._tls_option(credentials),
                f"docker://{reference}",
            ]
            returncode, stdout, stderr = await self._execute(command)
        if returncode:
            detail = self._detail(stdout, stderr, [credentials.password])
            if any(pattern in detail.casefold() for pattern in _MISSING_PATTERNS):
                return None
            raise RegistryError(detail)
        digest = stdout.decode("utf-8", errors="replace").strip().casefold()
        if not _DIGEST.fullmatch(digest):
            raise RegistryError("Skopeo returned an invalid container manifest digest")
        return digest

    async def estimate_size(self, reference: str, credentials: RegistryCredentials) -> int:
        repository = reference.rsplit("@", 1)[0]
        seen: set[str] = set()
        manifests_seen = 0

        async def visit(current_reference: str, expected_digest: str | None = None) -> int:
            nonlocal manifests_seen
            manifests_seen += 1
            if manifests_seen > 256:
                raise RegistryError("Container image contains more than 256 manifests")
            raw = await self._inspect_raw(current_reference, credentials)
            if len(raw) > 16 * 1024 * 1024:
                raise RegistryError("Container manifest exceeded the 16 MiB safety limit")
            if expected_digest is not None:
                actual_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
                if actual_digest != expected_digest:
                    raise RegistryError("Container manifest failed SHA-256 verification")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RegistryError("Container registry returned an invalid manifest") from exc
            if not isinstance(payload, dict):
                raise RegistryError("Container registry returned an invalid manifest")

            total = 0
            if expected_digest is not None and expected_digest not in seen:
                seen.add(expected_digest)
                total += len(raw)

            manifests = payload.get("manifests")
            if isinstance(manifests, list):
                for descriptor in manifests:
                    digest = self._descriptor_digest(descriptor)
                    if digest in seen:
                        continue
                    total += await visit(f"{repository}@{digest}", digest)
                return total

            descriptors: list[object] = []
            config = payload.get("config")
            if config is not None:
                descriptors.append(config)
            layers = payload.get("layers")
            if isinstance(layers, list):
                descriptors.extend(layers)
            blobs = payload.get("blobs")
            if isinstance(blobs, list):
                descriptors.extend(blobs)
            if config is None and not isinstance(layers, list) and not isinstance(blobs, list):
                raise RegistryError("Container registry returned an unsupported manifest type")
            for descriptor in descriptors:
                digest = self._descriptor_digest(descriptor)
                if digest in seen:
                    continue
                if not isinstance(descriptor, dict) or not isinstance(descriptor.get("size"), int):
                    raise RegistryError("Container manifest descriptor omitted its size")
                size = int(descriptor["size"])
                if size < 0:
                    raise RegistryError("Container manifest descriptor has a negative size")
                seen.add(digest)
                total += size
            return total

        digest = reference.rsplit("@", 1)[1] if "@" in reference else None
        return await visit(reference, digest)

    async def copy(
        self,
        source: str,
        destination: str,
        source_credentials: RegistryCredentials,
        destination_credentials: RegistryCredentials,
        expected_digest: str,
    ) -> None:
        with (
            self._authfile(source_credentials, destination_credentials) as authfile,
            tempfile.TemporaryDirectory(prefix="githarbor-skopeo-") as temporary,
        ):
            digest_file = Path(temporary) / "digest"
            command = [
                "skopeo",
                "copy",
                "--authfile",
                str(authfile),
                "--all",
                "--preserve-digests",
                "--retry-times",
                "2",
                "--digestfile",
                str(digest_file),
                *self._copy_tls_options(source_credentials, destination_credentials),
                f"docker://{source}",
                f"docker://{destination}",
            ]
            await self._run(
                command,
                [source_credentials.password, destination_credentials.password],
            )
            try:
                copied_digest = digest_file.read_text(encoding="utf-8").strip().casefold()
            except OSError as exc:
                raise RegistryError("Skopeo did not report the copied digest") from exc
        if copied_digest != expected_digest:
            raise RegistryError(
                "Destination container digest did not match the GitHub source digest"
            )

    async def list_tags(self, repository: str, credentials: RegistryCredentials) -> set[str]:
        with self._authfile(credentials) as authfile:
            command = [
                "skopeo",
                "list-tags",
                "--authfile",
                str(authfile),
                *self._tls_option(credentials),
                f"docker://{repository}",
            ]
            returncode, stdout, stderr = await self._execute(command)
        if returncode:
            detail = self._detail(stdout, stderr, [credentials.password])
            if any(pattern in detail.casefold() for pattern in _MISSING_PATTERNS):
                return set()
            raise RegistryError(detail)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RegistryError("Skopeo returned an invalid container tag list") from exc
        tags = payload.get("Tags") if isinstance(payload, dict) else None
        if not isinstance(tags, list):
            raise RegistryError("Skopeo returned an invalid container tag list")
        return {str(tag) for tag in tags}

    async def delete(self, reference: str, credentials: RegistryCredentials) -> None:
        with self._authfile(credentials) as authfile:
            command = [
                "skopeo",
                "delete",
                "--authfile",
                str(authfile),
                *self._tls_option(credentials),
                f"docker://{reference}",
            ]
            await self._run(command, [credentials.password])

    async def _inspect_raw(self, reference: str, credentials: RegistryCredentials) -> bytes:
        with self._authfile(credentials) as authfile:
            command = [
                "skopeo",
                "inspect",
                "--raw",
                "--authfile",
                str(authfile),
                *self._tls_option(credentials),
                f"docker://{reference}",
            ]
            return await self._run(command, [credentials.password])

    async def _run(self, command: Sequence[str], secrets: list[str]) -> bytes:
        returncode, stdout, stderr = await self._execute(command)
        if returncode:
            raise RegistryError(self._detail(stdout, stderr, secrets))
        return stdout

    async def _execute(self, command: Sequence[str]) -> tuple[int, bytes, bytes]:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            if process is not None:
                process.kill()
                await process.communicate()
            raise RegistryError(
                f"Container transfer timed out after {self.timeout_seconds} seconds"
            ) from exc
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
            raise
        except OSError as exc:
            raise RegistryError(f"Unable to execute Skopeo: {exc.__class__.__name__}") from exc
        assert process.returncode is not None
        return process.returncode, stdout, stderr

    @staticmethod
    def _detail(stdout: bytes, stderr: bytes, secrets: list[str]) -> str:
        detail = stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = stdout.decode("utf-8", errors="replace").strip()
        return redact(detail or "Container registry operation failed", secrets)[:4000]

    @staticmethod
    def _descriptor_digest(descriptor: object) -> str:
        if not isinstance(descriptor, dict):
            raise RegistryError("Container manifest contains an invalid descriptor")
        digest = str(descriptor.get("digest") or "").casefold()
        if not _DIGEST.fullmatch(digest):
            raise RegistryError("Container manifest contains an invalid SHA-256 digest")
        return digest

    @staticmethod
    def _tls_option(credentials: RegistryCredentials) -> list[str]:
        return [] if credentials.tls_verify else ["--tls-verify=false"]

    @staticmethod
    def _copy_tls_options(
        source: RegistryCredentials, destination: RegistryCredentials
    ) -> list[str]:
        options: list[str] = []
        if not source.tls_verify:
            options.append("--src-tls-verify=false")
        if not destination.tls_verify:
            options.append("--dest-tls-verify=false")
        return options

    @contextmanager
    def _authfile(self, *credentials: RegistryCredentials) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="githarbor-registry-auth-") as temporary:
            path = Path(temporary) / "auth.json"
            auths: dict[str, dict[str, str]] = {}
            for credential in credentials:
                encoded = base64.b64encode(
                    f"{credential.username}:{credential.password}".encode()
                ).decode("ascii")
                auths[credential.registry] = {"auth": encoded}
            path.write_text(json.dumps({"auths": auths}), encoding="utf-8")
            path.chmod(0o600)
            yield path
