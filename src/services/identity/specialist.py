"""Loads a specialist's own identity document - who a specialist is.

    IDENTITY.md            - who the Manager is       -> identity.py
    SOUL.md                - how the Manager behaves  -> soul.py
    USER.md                - who the agent serves     -> services/profile/
    identities/<key>.md    - who a specialist is      -> this module

Read by the *same* section parser as IDENTITY.md, so the two documents cannot
drift into different formats. What differs is the type they produce, and the
difference is the whole point of this module.

**A SpecialistIdentity has no capabilities field.**

Not "has one that is ignored" - it does not exist. A specialist's capabilities
are computed from the analyzers actually wired into it, and a document must
never be able to add to them. Making the field absent rather than ignored
turns that from a rule someone has to remember into something the type system
will not express:

    AgentIdentity        name, role, purpose, description, capabilities, ...
                         the Manager IS its declared capabilities, so the
                         document declares them and to_spec() carries them

    SpecialistIdentity   name, role, purpose, description, area_of_responsibility
                         a specialist's reach is wired, not written, so there
                         is nowhere for a document to put a claim

A ``## Capabilities`` section in a specialist document is therefore refused
outright, with a message saying where capabilities really come from. Silently
ignoring it would recreate exactly the defect this project just fixed in
IDENTITY.md - a heading that looks like loaded configuration and behaves like
a comment.

The name is the one thing here with structural consequence: it is what the
agent registers under, what selects its memory file, and what identifies it in
a report. That is deliberate and it is why there is a single derivation point
for all three - see src/agents/specialists.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.services.identity.identity import (
    IdentityError,
    joined_section,
    parse_sections,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_IDENTITIES_DIR = "identities"


@dataclass(frozen=True)
class SpecialistIdentity:
    """A specialist's declared identity. Describes; never grants.

    Every field is prose except ``name``. Nothing here reaches an AgentSpec's
    capability set, because there is nothing here that could.
    """

    name: str
    role: str = ""
    purpose: str = ""
    description: str = ""
    # Doubles as the specialist brain's reasoning domain when one is declared.
    # That is not a capability: a domain sentence shapes what an LLM brain is
    # asked to reason *about*, and a SpecialistBrain returns an Insight - it
    # cannot execute, delegate, send or reach a mailbox. See brain_factory.
    area_of_responsibility: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise IdentityError("a specialist identity must declare a name")
        object.__setattr__(self, "name", " ".join(self.name.split()))

    def describe(self) -> str:
        return f"{self.name}: {self.role or 'no role stated'}"


def parse_specialist_identity(text: str) -> SpecialistIdentity:
    """Parse a specialist identity document. Exposed so tests need no file."""
    sections = parse_sections(text)

    if "capabilities" in sections:
        raise IdentityError(
            "A specialist identity document must not declare '## Capabilities'. "
            "A specialist's capabilities are computed from the analyzers and "
            "tools actually wired into it, so a document cannot add to them. "
            "Remove the section - describing the specialist is what this file "
            "is for."
        )

    name = joined_section(sections, "name")
    if not name:
        raise IdentityError(
            "A specialist identity document has no '## Name' section. It must "
            "declare the name the specialist registers and remembers under."
        )

    return SpecialistIdentity(
        name=name,
        role=joined_section(sections, "role"),
        purpose=joined_section(sections, "purpose"),
        description=joined_section(sections, "description"),
        area_of_responsibility=joined_section(sections, "area of responsibility"),
    )


def identity_path(identities_dir: str | Path, key: str) -> Path:
    """Where the document for one specialist lives.

    ``key`` is the stable technical key the code knows a specialist by; the
    *name* inside the document is the human one and may be anything. Keeping
    them apart is what lets a specialist be renamed without the code that
    builds it having to be found and edited.
    """
    return Path(identities_dir) / f"{key}.md"


def load_specialist_identity(
    identities_dir: str | Path, key: str
) -> SpecialistIdentity | None:
    """Load one specialist's identity, or None when no document exists.

    None rather than an error: a specialist with no identity document keeps
    the name its module already declares, which is exactly the behaviour this
    project had before identity documents existed. Requiring the document
    would mean adding a file to keep working code working.

    An unreadable or invalid document is a different matter and does raise -
    a document that exists is meant to take effect, and quietly falling back
    would leave a specialist running under a name nobody chose.
    """
    path = identity_path(identities_dir, key)
    if not path.exists():
        logger.debug("no identity document for '%s' at '%s'", key, path)
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IdentityError(f"Could not read '{path}': {exc}") from exc

    identity = parse_specialist_identity(text)
    logger.info("loaded specialist identity: %s", identity.describe())
    return identity
