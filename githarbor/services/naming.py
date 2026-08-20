from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_component(value: str, max_length: int = 120) -> str:
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
        safe_owner = safe_component(owner, 80)
        suffix = f"--gh{github_id}"
        prefix = f"{safe_owner}--{safe_name}"
        return f"{prefix[: 255 - len(suffix)].rstrip('.-')}{suffix}"
    raise ValueError(f"Unknown repository kind: {kind}")
