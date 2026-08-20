from __future__ import annotations

import re
from collections.abc import Iterable

_URL_CREDENTIALS = re.compile(r"(https?://)([^/@\s]+)@", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(r"(?i)(token|password|authorization|secret)(\s*[:=]\s*)([^\s,;]+)")
_AUTH_HEADER = re.compile(r"(?i)(authorization:\s*)(?:bearer|token|basic)\s+\S+")


def redact(value: str, secrets: Iterable[str] = ()) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", result)
    result = _AUTH_HEADER.sub(r"\1[REDACTED]", result)
    result = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", result)
    return result
