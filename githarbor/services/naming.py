from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_REPOSITORY_NAME = 100


def safe_component(value: str, max_length: int = _MAX_REPOSITORY_NAME) -> str:
    value = _UNSAFE.sub("-", value.strip()).strip(".-")
    value = re.sub(r"-+", "-", value)
    if not value:
        raise ValueError("Repository name has no safe destination characters")
    return value[:max_length].rstrip(".-")


def destination_name(owner: str, name: str, github_id: int, kind: str) -> str:
    safe_name = safe_component(name)
    if kind == "owned":
        return safe_name
    if kind == "starred":
        safe_owner = safe_component(owner)
        prefix = f"{safe_owner}--{safe_name}"
        return prefix[:_MAX_REPOSITORY_NAME].rstrip(".-")
    raise ValueError(f"Unknown repository kind: {kind}")


def collision_destination_name(owner: str, name: str, github_id: int, kind: str) -> str:
    preferred = destination_name(owner, name, github_id, kind)
    if kind != "starred":
        return preferred
    suffix = f"--gh{github_id}"
    return f"{preferred[: _MAX_REPOSITORY_NAME - len(suffix)].rstrip('.-')}{suffix}"
