"""Recognising a credential written down where it should not be.

Any memory that retains information about a person needs this check, and there
is now more than one policy that needs it. It lives beside Memory rather than
inside a particular policy so a second one does not have to reinvent it.

The rule throughout: err toward refusing. A false positive costs one memory
that has to be reworded; a false negative writes a credential into a store that
later gets recalled into a prompt. Those are not comparable costs.

Detection matches the *shape* of a secret - an assignment, a known key prefix,
a PEM header - rather than the bare words, so "the user stores credentials in
environment variables" is not refused while "password: hunter2" is.

Note: src/manager/memory_policy.py carries its own equivalent of this, written
before a second policy existed. Consolidating the two means editing a working,
tested module, so it is left alone until that is deliberately approved.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# Context keys that make a record a secret regardless of what it says. Matched
# as whole words after normalising separators, so "api_key" and "api-key" are
# caught while an innocent "keywords" is not.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "access_token", "refresh_token", "credential", "credentials",
        "private_key", "client_secret", "authorization", "auth",
    }
)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "an API key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "an access token"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"), "an API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"), "a bearer token"),
    (
        re.compile(
            r"\b(password|passwd|secret|api[_\-]?key|access[_\-]?token|"
            r"refresh[_\-]?token|client[_\-]?secret)\b\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
        "a credential",
    ),
)


def _normalise(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def looks_like_a_secret(
    content: str = "",
    tags: Iterable[str] = (),
    context: Mapping[str, Any] | None = None,
) -> str | None:
    """What kind of secret this appears to hold, or None.

    Returns a *description* ("an API key"), never the value itself, so a caller
    reporting the refusal cannot accidentally log the thing it refused.
    """
    for pattern, description in _SECRET_PATTERNS:
        if pattern.search(content or ""):
            return description

    for tag in tags or ():
        if _normalise(tag) in SECRET_KEYS:
            return "a value tagged as a credential"

    for key in context or {}:
        if _normalise(key) in SECRET_KEYS:
            return f"a credential under the '{key}' key"

    return None
