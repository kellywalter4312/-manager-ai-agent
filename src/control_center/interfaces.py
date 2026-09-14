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

from typing import Mapping

import re

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


class SurfaceKind(str):
    """One kind of interaction surface - an open, normalised name, never a
    closed list (Pre-Stage-9 Campaign 5, SRF-01).

    ``InterfaceFeature`` above stays the catalogue of kinds this project
    already knows how to describe; an agent may declare any other kind its
    work genuinely has ("timeline", "map", "board") without this module - or
    any other - changing. Compared and hashed as its plain lower-case value,
    so ``InterfaceFeature.WORKSPACE`` and "workspace" are the same surface.
    """

    def __new__(cls, value: "str | InterfaceFeature") -> "SurfaceKind":
        raw = value.value if isinstance(value, InterfaceFeature) else str(value)
        cleaned = "_".join(raw.strip().lower().split())
        if not cleaned or not re.fullmatch(r"[a-z0-9_]{1,40}", cleaned):
            raise ValueError(f"a surface kind must be a short plain name, got {value!r}")
        return super().__new__(cls, cleaned)

    @property
    def value(self) -> str:
        return str(self)

    @property
    def is_workspace_like(self) -> bool:
        """Whether this surface implies work that continues between turns -
        answered from the known catalogue; an unknown kind makes no claim."""
        try:
            return InterfaceFeature(str(self)).is_workspace_like
        except ValueError:
            return False


def surface_kinds(values) -> "frozenset[SurfaceKind]":
    return frozenset(SurfaceKind(value) for value in values or ())


# A governed surface's state, as the evolution handler stores it under
# ``surface.<agent>.<kind>`` in the governed config.
SURFACE_IMPLEMENTED = "implemented"
SURFACE_PLANNED = "planned"
SURFACE_WITHDRAWN = "withdrawn"
SURFACE_STATES = (SURFACE_IMPLEMENTED, SURFACE_PLANNED, SURFACE_WITHDRAWN)
SURFACE_COMPONENT_PREFIX = "surface."


def surface_agent_key(agent_name: str) -> str:
    return "_".join(str(agent_name or "").strip().lower().split())


def surface_component_id(agent_name: str, kind: "str | InterfaceFeature") -> str:
    return f"{SURFACE_COMPONENT_PREFIX}{surface_agent_key(agent_name)}.{SurfaceKind(kind)}"


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
    features: "frozenset[SurfaceKind]" = field(
        default_factory=lambda: frozenset({SurfaceKind(InterfaceFeature.CHAT)})
    )
    summary: str = ""
    implemented: "frozenset[SurfaceKind]" = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.agent.strip():
            raise ValueError("InterfaceDescriptor.agent must not be empty")
        object.__setattr__(self, "features", surface_kinds(self.features))
        # A surface cannot be implemented without being offered. Anything else
        # would mean a screen rendering something the specialist never claimed.
        object.__setattr__(
            self, "implemented", surface_kinds(self.implemented) & self.features
        )

    @property
    def has_workspace(self) -> bool:
        """Whether this specialist wants more than a conversation."""
        return any(feature.is_workspace_like for feature in self.features)

    @property
    def planned(self) -> frozenset[InterfaceFeature]:
        """Declared, but not yet something the Control Center can render."""
        return self.features - self.implemented

    def offers(self, feature: "str | InterfaceFeature") -> bool:
        return SurfaceKind(feature) in self.features

    def can_render(self, feature: "str | InterfaceFeature") -> bool:
        return SurfaceKind(feature) in self.implemented

    def governed(self, values: "Mapping[str, str] | None") -> "InterfaceDescriptor":
        """This descriptor with the governed surface states for this agent
        applied: an ``implemented`` or ``planned`` entry adds or moves a surface,
        ``withdrawn`` removes it. The agent's own declaration is the default;
        the governed config is the owner's approved evolution of it."""
        if not values:
            return self
        key = surface_agent_key(self.agent)
        prefix = f"{SURFACE_COMPONENT_PREFIX}{key}."
        features, implemented = set(self.features), set(self.implemented)
        for component_id, state in values.items():
            if not str(component_id).startswith(prefix):
                continue
            try:
                kind = SurfaceKind(str(component_id)[len(prefix):])
            except ValueError:
                continue
            if state == SURFACE_WITHDRAWN:
                features.discard(kind)
                implemented.discard(kind)
            elif state == SURFACE_IMPLEMENTED:
                features.add(kind)
                implemented.add(kind)
            elif state == SURFACE_PLANNED:
                features.add(kind)
                implemented.discard(kind)
        return InterfaceDescriptor(
            agent=self.agent, display_name=self.display_name, features=frozenset(features),
            summary=self.summary, implemented=frozenset(implemented),
        )

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


def describe_interface(agent: Any, governed: "Mapping[str, str] | None" = None) -> InterfaceDescriptor:
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
        return declared.governed(governed)

    summary = ""
    spec = getattr(agent, "spec", None)
    if spec is not None:
        summary = getattr(spec, "description", "") or ""
    return default_descriptor(getattr(agent, "name", "unknown"), summary).governed(governed)
