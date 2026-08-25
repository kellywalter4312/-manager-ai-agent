"""What kind of surface a specialist offers, discovered rather than assumed.

Today every specialist is reached the same way: talk to the Manager, who
delegates, and a result comes back. That stays true and stays the default.
But it is not the
only shape work takes - coding is iterative, research accumulates sources, a
mailbox is something you look through - and a Control Center that assumed
"chat, then a result" would need rebuilding the first time a specialist needed
a workspace.

So a specialist *declares* what it offers, and the Control Center reads the
declaration:

    registry -> specialist -> InterfaceDescriptor -> what to render

**No specialist is named here.** This module contains no agent name and no
list of future agents, exactly as the rest of the Control Center does not. A
specialist that arrives later declaring FILES and CODE gets a workspace without
a line of this changing; one that declares nothing gets the conversational
default, which is a complete and honest answer rather than a gap.

**Declaring a feature is not having it.** A feature is a claim about the shape
of an interaction, not a grant of capability. Declaring CODE does not create a
coding tool, does not widen what an agent may do, and does not let it touch a
file - capability still comes from wired analyzers and tools, and Trust in
Stages is unmoved. All a feature does is tell a screen what to draw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InterfaceFeature(str, Enum):
    """A kind of interaction surface a specialist may offer.

    Kinds of surface, never subjects - the same rule capabilities live under.
    There is no "email tab" or "coding tab" here: there is a mailbox surface
    and a code surface, and any specialist whose work genuinely has that shape
    may declare it.
    """

    CHAT = "chat"                 # conversation with this specialist
    ACTIVITY = "activity"         # what it has been doing
    MAILBOX = "mailbox"           # a collection of messages to look through
    OPPORTUNITIES = "opportunities"  # ranked findings
    FILES = "files"               # a file tree to inspect
    CODE = "code"                 # code to read, with explanation
    WORKSPACE = "workspace"       # iterative work with state between turns
    SOURCES = "sources"           # gathered references

    @property
    def is_workspace_like(self) -> bool:
        """Whether this surface implies work that continues between turns."""
        return self in (
            InterfaceFeature.FILES,
            InterfaceFeature.CODE,
            InterfaceFeature.WORKSPACE,
            InterfaceFeature.SOURCES,
        )


@dataclass(frozen=True)
class InterfaceDescriptor:
    """What one specialist says about how it wants to be interacted with.

    A description, not a permission. Every field here changes what a screen
    offers and nothing about what the agent may do.

    ``implemented`` is the honest half. A specialist may describe a surface the
    system does not yet render - that is how an architecture makes room for
    something - and the Control Center must show that as planned rather than
    available. Claiming a workspace exists because a descriptor mentioned one
    would be the same lie as claiming a tool exists because a capability named
    it.
    """

    agent: str
    display_name: str
    features: frozenset[InterfaceFeature] = field(
        default_factory=lambda: frozenset({InterfaceFeature.CHAT})
    )
    summary: str = ""
    implemented: frozenset[InterfaceFeature] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.agent.strip():
            raise ValueError("InterfaceDescriptor.agent must not be empty")
        object.__setattr__(self, "features", frozenset(self.features))
        # A surface cannot be implemented without being offered. Anything else
        # would mean a screen rendering something the specialist never claimed.
        object.__setattr__(
            self, "implemented", frozenset(self.implemented) & frozenset(self.features)
        )

    @property
    def has_workspace(self) -> bool:
        """Whether this specialist wants more than a conversation."""
        return any(feature.is_workspace_like for feature in self.features)

    @property
    def planned(self) -> frozenset[InterfaceFeature]:
        """Declared, but not yet something the Control Center can render."""
        return self.features - self.implemented

    def offers(self, feature: InterfaceFeature) -> bool:
        return feature in self.features

    def can_render(self, feature: InterfaceFeature) -> bool:
        return feature in self.implemented

    def describe(self) -> str:
        available = ", ".join(sorted(f.value for f in self.implemented)) or "chat only"
        pending = ", ".join(sorted(f.value for f in self.planned))
        return available + (f" (planned: {pending})" if pending else "")


# What a specialist gets when it declares nothing. Conversation through the
# Manager is a complete way to work with an agent, so the default is a real
# answer rather than an absence to be filled in later.
def default_descriptor(agent_name: str, summary: str = "") -> InterfaceDescriptor:
    return InterfaceDescriptor(
        agent=agent_name,
        display_name=agent_name,
        features=frozenset({InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY}),
        implemented=frozenset({InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY}),
        summary=summary,
    )


def describe_interface(agent: Any) -> InterfaceDescriptor:
    """What surface this agent offers, asked of the agent itself.

    Duck-typed, in the same spirit as `_provider_name` in cognition: an agent
    that publishes an ``interface`` descriptor is taken at its word, and one
    that does not gets the conversational default. That is what lets a future
    specialist bring its own surface without this module learning it exists,
    and what stops a specialist being required to know about the Control
    Center in order to be usable by it.
    """
    declared = getattr(agent, "interface", None)
    if isinstance(declared, InterfaceDescriptor):
        return declared

    summary = ""
    spec = getattr(agent, "spec", None)
    if spec is not None:
        summary = getattr(spec, "description", "") or ""
    return default_descriptor(getattr(agent, "name", "unknown"), summary)
