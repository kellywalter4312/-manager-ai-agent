"""The celebration-reaction contract - a presentation-level response to a
genuine milestone, kept structurally separate from what that milestone
*means*.

The same abstraction move `src/providers/live_info.py` already made for
current information, applied to presentation: anything that wants a
celebration reaction depends on this interface, never on a specific
animation implementation, a specific renderer, or a specific agent's
identity hardcoded into the selection logic.

**Why this is not the Brain, and why the Brain never touches it.** The
architectural boundary the brief states directly:

    Memory / Milestone event
      -> Brain + Personality decide meaning/words   ("Happy birthday, Sam.")
      -> CelebrationReaction is selected             (this module)
      -> VisualEnvironment / Avatar presents it       (a future seam - see
                                                        this stage's own
                                                        report for why that
                                                        last hop is not
                                                        wired yet)

A ``CelebrationReaction`` carries no words, no reasoning, and no memory
reference - only presentation-shaped fields (an avatar action, an
environment action, an overlay behaviour, an intensity, a duration). The
sentence a person actually reads or hears is composed by
``manager/capabilities.py``'s own ``_celebrate`` through the Brain
exactly as it already was; this module has no method that could produce
text and no way to reach a Brain even if it wanted to.

**Why a reaction carries an agent key and a personality, not an agent.**
Exactly the same narrowing every other collaborator in this project
already lives under (``CapabilityRequest``'s own docstring: "there is no
such object in scope"). A provider selecting a reaction is handed what it
needs to vary the *style* of the reaction - which agent, and what that
agent's own declared ``AgentPersonality`` already says about how it
communicates - never the agent itself, never its Brain, never its Memory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.identity.personality import AgentPersonality

# The only three levels a person can choose - "Off / Subtle / Full", the
# brief's own words. A closed set, not a numeric scale: a provider must
# recognise all three, and a caller must never invent a fourth.
INTENSITY_LEVELS: tuple[str, ...] = ("off", "subtle", "full")


@dataclass(frozen=True)
class CelebrationReaction:
    """One presentation-level reaction to a genuine milestone.

    Every field is either an identity (what this is, who it is for, what
    version) or a *presentation* instruction (an avatar action, an
    environment action, an overlay, an intensity, a duration) - never
    content, never a reason, never a reference to anything reasoning or
    remembering. A future presentation layer reads this and decides how
    to render each field; this type has no opinion about pixels, and
    none about words.

    ``enabled=False`` is a real, first-class value - the honest shape for
    "no reaction" (celebration turned off, or an unrecognised milestone
    type) - rather than ``None`` standing in for it. A caller can always
    ask ``reaction.enabled`` without a null check, the same discipline
    ``ToolResult``/``LiveInfoResult`` already apply to their own "nothing
    to report" cases.
    """

    milestone_type: str
    agent_key: str
    reaction_id: str
    intensity: str
    avatar_action: str = ""
    environment_action: str = ""
    overlay: str = ""
    duration_ms: int = 0
    version: int = 1
    enabled: bool = True

    def __post_init__(self) -> None:
        if not str(self.milestone_type).strip():
            raise ValueError("CelebrationReaction.milestone_type must not be empty")
        if not str(self.agent_key).strip():
            raise ValueError("CelebrationReaction.agent_key must not be empty")
        if not str(self.reaction_id).strip():
            raise ValueError("CelebrationReaction.reaction_id must not be empty")
        if self.intensity not in INTENSITY_LEVELS:
            raise ValueError(
                f"CelebrationReaction.intensity must be one of {INTENSITY_LEVELS}, "
                f"got {self.intensity!r}"
            )
        if self.duration_ms < 0:
            raise ValueError("CelebrationReaction.duration_ms must not be negative")
        object.__setattr__(self, "milestone_type", " ".join(str(self.milestone_type).split()))
        object.__setattr__(self, "agent_key", " ".join(str(self.agent_key).split()).lower())
        object.__setattr__(self, "reaction_id", " ".join(str(self.reaction_id).split()))

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload - the boundary format a future
        presentation layer (or, today, a TaskResult payload) reads,
        mirroring ``src/control_center/snapshot.py``'s own "a boundary
        format, read once by something in another language" reasoning."""
        return {
            "milestone_type": self.milestone_type,
            "agent_key": self.agent_key,
            "reaction_id": self.reaction_id,
            "intensity": self.intensity,
            "avatar_action": self.avatar_action,
            "environment_action": self.environment_action,
            "overlay": self.overlay,
            "duration_ms": self.duration_ms,
            "version": self.version,
            "enabled": self.enabled,
        }

    def describe(self) -> str:
        """A short line for logs. Excludes nothing - unlike a
        ``MemoryRecord`` or a ``ToolResult``, a reaction carries no
        content that would need to stay out of a log file."""
        state = "enabled" if self.enabled else "disabled"
        return (
            f"celebration {self.reaction_id} v{self.version} "
            f"({self.intensity}, {state}) for {self.agent_key}/{self.milestone_type}"
        )


class CelebrationProvider(ABC):
    """A source of celebration reactions.

    One method, the same narrow shape ``LiveInfoProvider``/``LLMProvider``
    already proved cheap to implement and cheap to replace. A default,
    deterministic implementation, a future themed implementation, and a
    test double all satisfy this identically - and none of them needs to
    know anything about ``ManagerAgent``, Brain, Memory, the LLM
    provider, or the voice/speech provider to do it.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "default"."""

    @abstractmethod
    def select(
        self,
        *,
        milestone_type: str,
        agent_key: str,
        personality: "AgentPersonality | None",
        intensity: str,
        occurrence: int = 0,
    ) -> CelebrationReaction:
        """Choose a reaction for one genuine milestone.

        ``personality`` is this agent's own declared ``AgentPersonality``
        (or None for an agent that declares none) - the existing,
        canonical source for "how does this agent tend to express
        itself", read here rather than a new, parallel per-agent
        configuration. ``intensity`` is the already-resolved user
        preference ("off"/"subtle"/"full") - this method does not read
        preferences itself, the same narrowing every other collaborator
        in this project already lives under. ``occurrence`` is how many
        times this ``agent_key``/``milestone_type`` pair has already been
        celebrated, so an implementation *may* vary its choice across
        repeated occurrences - never required to produce the identical
        reaction forever, and never required to remember anything to do
        it (see ``DefaultCelebrationProvider`` for a purely deterministic
        example).

        Must never raise for an ordinary, valid input; an unrecognised
        milestone type or a disabled intensity is answered with an
        honestly disabled reaction (``enabled=False``), never an
        exception - the same "describe, don't fail" contract every other
        provider in this project already keeps.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
