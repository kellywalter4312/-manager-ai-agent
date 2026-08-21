"""Loads IDENTITY.md - who the Manager is.

The AGENT framework's three context documents each have a distinct owner:

    IDENTITY.md - who the agent is        -> this module
    SOUL.md     - how the agent behaves   -> soul.py
    USER.md     - who the agent serves    -> services/profile/

This one produces the Manager's ``AgentSpec``: the same contract every
specialist implements. Because the spec is read from a document rather than
hardcoded, changing what the Manager takes on itself is a file edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.contracts import AgentSpec
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_IDENTITY_PATH = "IDENTITY.md"

_HEADING = re.compile(r"^#{2,3}\s*(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


class IdentityError(Exception):
    """Raised when an identity document is missing or unusable."""


@dataclass(frozen=True)
class AgentIdentity:
    """The Manager's declared identity, as read from IDENTITY.md."""

    name: str
    role: str = ""
    description: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    delegates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise IdentityError("IDENTITY.md must declare a name")
        if not self.capabilities:
            raise IdentityError("IDENTITY.md must declare at least one capability")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(
            self,
            "capabilities",
            frozenset(c.strip().lower() for c in self.capabilities if c.strip()),
        )

    def to_spec(self) -> AgentSpec:
        """The AgentSpec the registry and router will see.

        Falls back to the role line when no description is given, because
        AgentSpec requires a non-empty description.
        """
        return AgentSpec(
            name=self.name,
            description=self.description or self.role or f"{self.name} agent",
            capabilities=self.capabilities,
        )

    def describe(self) -> str:
        return f"{self.name}: {len(self.capabilities)} capability(ies)"


def parse_identity(text: str) -> AgentIdentity:
    """Parse IDENTITY.md. Exposed separately so tests need no file."""
    sections: dict[str, list[str]] = {}
    current = ""

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line.strip())

    def joined(key: str) -> str:
        return " ".join(sections.get(key, [])).strip()

    def items(key: str) -> list[str]:
        values: list[str] = []
        for line in sections.get(key, []):
            content = _BULLET.sub(r"\1", line)
            values.extend(part.strip() for part in content.split(",") if part.strip())
        return values

    name = joined("name")
    if not name:
        raise IdentityError(
            "IDENTITY.md has no '## Name' section. It must declare the agent's name."
        )

    # A delegate line reads "email_agent - reading mailboxes"; keep the name.
    delegates = tuple(
        entry.split(" - ")[0].split(" — ")[0].strip()
        for entry in sections.get("delegates", [])
        if _BULLET.match(entry) or entry.strip()
    )

    return AgentIdentity(
        name=name,
        role=joined("role"),
        description=joined("description"),
        capabilities=frozenset(items("capabilities")),
        delegates=tuple(d.lstrip("- ").strip() for d in delegates if d.strip()),
    )


def load_identity(path: str | Path = DEFAULT_IDENTITY_PATH) -> AgentIdentity:
    """Load the Manager's identity from disk."""
    identity_file = Path(path)
    if not identity_file.exists():
        raise IdentityError(
            f"No identity document at '{identity_file}'. IDENTITY.md defines who "
            f"the Manager is and which capabilities it handles itself."
        )
    try:
        text = identity_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise IdentityError(f"Could not read '{identity_file}': {exc}") from exc

    identity = parse_identity(text)
    logger.info("loaded identity: %s", identity.describe())
    return identity
