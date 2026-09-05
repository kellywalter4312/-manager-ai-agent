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
from src.services.identity.personality import AgentPersonality, parse_personality
from src.services.identity.voice import VoiceActivation, VoiceProfile, parse_voice
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
    # Why this agent exists, as distinct from what it is or what it does.
    # ``role`` answers "what is it" and ``description`` answers "what does it
    # do"; neither states what it is *for*. Kept as its own field rather than
    # folded into either, because a purpose that can only be inferred from a
    # job description is not a durable statement of intent.
    purpose: str = ""
    description: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    delegates: tuple[str, ...] = ()
    # The scope prose the document has always carried. It was previously
    # collected while parsing and then dropped, which made it a heading that
    # looked like loaded configuration and was not. Now it loads.
    area_of_responsibility: str = ""
    # How this agent sounds. Loaded, not decorative: a `## Voice` section is
    # parsed into this field, and it was added to the parser in the same
    # change that added it to the document - this project has twice been
    # bitten by a heading that looked like configuration and behaved like a
    # comment, and a third time would be a choice.
    #
    # Describes, never grants. A voice changes what an agent sounds like and
    # nothing about what it may do.
    voice: VoiceProfile | None = None
    # How this agent is recognized as being addressed by voice - a distinct
    # fact from voice above (see src/services/identity/voice.py's module
    # docstring). A `## Voice Activation` section is parsed into this field;
    # None means the document declared no phrases of its own, and a caller
    # falls back to a name-derived default (`default_activation`) rather
    # than requiring every document to declare one explicitly.
    activation: VoiceActivation | None = None
    # How this agent tends to communicate - a distinct dimension from voice
    # above (see src/services/identity/personality.py's module docstring:
    # voice is how it sounds, personality is how it tends to express
    # itself). A `## Personality` section is parsed into this field; None
    # means the document declared none, and a caller falls back to a
    # name-derived, unconfigured default (`default_personality`) rather
    # than requiring every document to declare one explicitly.
    personality: AgentPersonality | None = None

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

        ``purpose`` and ``area_of_responsibility`` deliberately do **not**
        appear here, and that omission is the point. AgentSpec is the routing
        contract - a name, a description, and the capability set a router
        scores a task against. Folding identity prose into it would let a
        sentence in a markdown file widen what gets routed to this agent.

        Identity describes; it does not grant. Capabilities come from the
        ``## Capabilities`` section alone, and nothing an author writes about
        why the agent exists can add to them.
        """
        return AgentSpec(
            name=self.name,
            description=self.description or self.role or f"{self.name} agent",
            capabilities=self.capabilities,
        )

    def describe(self) -> str:
        return f"{self.name}: {len(self.capabilities)} capability(ies)"


def parse_sections(text: str) -> dict[str, list[str]]:
    """Split an identity-style markdown document into its sections.

    Keyed by lower-cased heading; the value is the non-blank lines beneath it.
    Extracted from parse_identity so the specialist identity document is read
    by the *same* parser rather than a second one written to look like it.
    Two parsers drifting apart is how documents that appear to share a format
    quietly stop doing so.
    """
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

    return sections


def joined_section(sections: dict[str, list[str]], key: str) -> str:
    """One section's prose, collapsed onto a single line."""
    return " ".join(sections.get(key, [])).strip()


def listed_section(sections: dict[str, list[str]], key: str) -> list[str]:
    """One section read as a comma-or-bullet separated list."""
    values: list[str] = []
    for line in sections.get(key, []):
        content = _BULLET.sub(r"\1", line)
        values.extend(part.strip() for part in content.split(",") if part.strip())
    return values


def bulleted_section(sections: dict[str, list[str]], key: str) -> list[str]:
    """One section read as bulleted lines only, everything else ignored.

    Unlike ``listed_section``, a non-bulleted line is dropped rather than
    treated as list content - the same rule ``## Delegates`` parsing
    already applies inline, pulled out here so a second section can use it
    without copying it. What that makes possible: a section can carry
    explanatory prose *before* its list, the same way ``## Voice`` and
    ``## Delegates`` already do, without that prose being mistaken for an
    entry - a mistake ``listed_section`` would make, because it has no way
    to tell a sentence from an item.
    """
    return [
        _BULLET.match(line).group(1).strip()
        for line in sections.get(key, [])
        if _BULLET.match(line)
    ]


def parse_activation(sections: dict[str, list[str]], key: str) -> VoiceActivation | None:
    """Read a ``## Voice Activation`` section into an activation identity,
    or None when the document declares no phrases of its own.

    Shared by both AgentIdentity and SpecialistIdentity - see
    src/services/identity/specialist.py - for the same reason the section
    parser itself is shared: two documents that appear to have one format
    must not quietly acquire two. One phrase per bulleted line, exactly the
    shape ``## Delegates`` already uses - a phrase list benefits from the
    same explanatory prose that section carries, so this reads bulleted
    lines only rather than every line in the section.

    None rather than an empty VoiceActivation on purpose - "no phrases
    declared" and "a caller should fall back to a name-derived default" are
    the same fact here, and the caller (which knows the agent's name) is
    what actually produces that default; see ``default_activation``.
    """
    phrases = bulleted_section(sections, "voice activation")
    if not phrases:
        return None
    return VoiceActivation(key=key, phrases=tuple(phrases))


def parse_identity(text: str) -> AgentIdentity:
    """Parse IDENTITY.md. Exposed separately so tests need no file."""
    sections = parse_sections(text)

    def joined(key: str) -> str:
        return joined_section(sections, key)

    def items(key: str) -> list[str]:
        return listed_section(sections, key)

    name = joined("name")
    if not name:
        raise IdentityError(
            "IDENTITY.md has no '## Name' section. It must declare the agent's name."
        )

    # A delegate line reads "- Arthur - reading mailboxes"; keep the name.
    #
    # Bulleted lines only. The section is a list, and a sentence explaining
    # what the list means is not a delegate - but `or entry.strip()` used to
    # accept every non-blank line, so any prose written here became a delegate
    # named after its own first clause. Nothing consumed the field, so nothing
    # failed; it simply produced quiet nonsense the moment the document said
    # anything about itself.
    delegates = tuple(
        entry.split(" - ")[0].split(" — ")[0].strip()
        for entry in sections.get("delegates", [])
        if _BULLET.match(entry)
    )

    return AgentIdentity(
        name=name,
        role=joined("role"),
        purpose=joined("purpose"),
        description=joined("description"),
        capabilities=frozenset(items("capabilities")),
        delegates=tuple(d.lstrip("- ").strip() for d in delegates if d.strip()),
        # Read through the same helper as every other prose section. Both are
        # optional: a document that omits them is still a valid identity, so
        # nothing that parsed before this change stops parsing now.
        area_of_responsibility=joined("area of responsibility"),
        # Keyed on the agent's own name unless the document says otherwise,
        # so two agents cannot collide on a voice key by omission.
        voice=parse_voice(sections.get("voice", []), key=name),
        activation=parse_activation(sections, key=name),
        personality=parse_personality(sections.get("personality", []), key=name),
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
