"""A specialist's reasoning capability.

The orchestrator has a brain; so should each specialist. But they are not the
same abstraction, and this file exists rather than reusing ManagerBrain because
the two answer different questions:

    ManagerBrain    "what should happen next, and who should do it?"
    SpecialistBrain "what do I make of this work in front of me?"

Look at what an Insight cannot say. There is no DELEGATE, no ESCALATE, no
HANDLE_SELF - no action vocabulary of any kind. A specialist brain observes and
explains; it has no words for directing anyone. That is deliberate and it is
the point: a specialist must not be able to steer the orchestrator, and the
cleanest way to guarantee that is to give it nothing to steer with. Compare
BrainDecision, which exists precisely to recommend an action.

The method is ``consider`` rather than ``deliberate`` for the same reason
OpportunityInterpreter does not implement Analyzer: a different name makes it
obvious at every call site that these two brains are not substitutable, and
stops one being passed where the other is expected.

Deliberately absent, each a later and separate decision:

  - memory. A brain reasons; retaining is a different responsibility with a
    different lifecycle. A specialist holds both, side by side - see
    CognitiveAgent - and neither is built into the other.
  - any model. This is provider-agnostic, so a brain backed by a language
    model, by rules, or by nothing at all satisfies the same contract.
    NullSpecialistBrain is the default and needs no key and no network.
  - any domain. Nothing here knows about mail, calendars or messaging. A
    specialist supplies that when it implements the interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.core.contracts import Task


class SpecialistBrainError(Exception):
    """Raised when an insight cannot be formed into a valid contract."""


# Marks a note as something the specialist remembered, rather than something
# its caller established just now. Defined here, beside the contract, because
# both the agent that labels notes and anything that renders them for a model
# need to agree on the marker - and a brain must never mistake its own history
# for an instruction.
MEMORY_NOTE_PREFIX = "recalled"


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = [" ".join(str(v).split()) for v in values or ()]
    return tuple(dict.fromkeys(v for v in cleaned if v))


@dataclass(frozen=True)
class Insight:
    """What a specialist's brain made of a piece of work.

    An observation, never an instruction. Frozen like every other contract
    here: an insight records what was concluded at a moment, and whatever acts
    on it must not be able to edit it afterwards.

    ``unknowns`` is not decoration. A brain that cannot say what it failed to
    determine will present a partial reading as a complete one, and this
    project has consistently refused that - the same reasoning behind
    LLMJudgement.unknowns and Opportunity.missing_fields.
    """

    summary: str
    confidence: float = 0.0
    source: str = ""
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # An insight that says nothing cannot be acted on or reviewed.
        if not str(self.summary).strip():
            raise SpecialistBrainError("Insight must say something")
        object.__setattr__(self, "summary", " ".join(str(self.summary).split()))

        if not 0.0 <= float(self.confidence) <= 1.0:
            raise SpecialistBrainError(
                f"Insight.confidence must be between 0 and 1, got {self.confidence}"
            )
        object.__setattr__(self, "confidence", float(self.confidence))

        object.__setattr__(self, "source", str(self.source).strip())
        object.__setattr__(self, "unknowns", _clean(self.unknowns))

    @property
    def is_useful(self) -> bool:
        """Whether this insight adds anything a caller can act on."""
        return self.confidence > 0.0

    def describe(self) -> str:
        """A short line for logs.

        Excludes ``summary``: a specialist reasons over its own domain content,
        which for the mail specialist means message text. The same rule
        Completion.describe() and LLMJudgement.describe() follow.
        """
        via = f" via={self.source}" if self.source else ""
        return (
            f"insight confidence={self.confidence:.2f}{via} "
            f"unknowns={len(self.unknowns)}"
        )

    @classmethod
    def none(cls, reason: str, source: str = "") -> "Insight":
        """A valid insight meaning "I have nothing to add"."""
        return cls(summary=reason, confidence=0.0, source=source, unknowns=("no reasoning",))


class SpecialistBrain(ABC):
    """A reasoning capability a specialist can consult about its own work."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used for provenance and logs."""

    @abstractmethod
    def consider(self, task: Task, notes: Sequence[str] = ()) -> Insight:
        """Form a view about this piece of work.

        ``notes`` is free context the specialist wants considered - its own
        recalled memory, typically. It is inert text: a brain receives what it
        is told and holds no route back to any store.

        Note what is absent from the signature. There is no roster of agents,
        because a specialist has no business knowing who else exists, and
        nothing to delegate to even if it did.

        Implementations should return an insight rather than raise. A caller
        guards this boundary anyway, because a brain is injected and may be
        anything.
        """

    def converse(self, task: Task, notes: Sequence[str] = ()) -> Insight:
        """Compose an open-ended, conversational answer - never a
        judgement about this specialist's own domain work.

        A genuinely different question from ``consider()``'s ("what do I
        make of this piece of work") - this one is "what is the actual
        answer to this", for a remark that named none of this
        specialist's own domain vocabulary and needs a real, general
        answer instead. See ``CognitiveAgent.general_conversation`` for
        the one, reusable place this is called from - shared by every
        specialist rather than reimplemented per agent, the identical
        role ``ManagerBrain.converse`` plays for the orchestrator.

        Concrete, not abstract, and defaulting to an honest "no view" -
        the same reason ``ManagerBrain.converse`` defaults this way, so
        every existing ``SpecialistBrain`` implementation and test double
        keeps working unchanged. Only a brain that can genuinely converse
        needs to override this (see ``LLMSpecialistBrain.converse``).
        """
        return Insight.none(
            reason="no reasoning capability is configured", source=self.name
        )


class NullSpecialistBrain(SpecialistBrain):
    """The default: no reasoning capability configured.

    Not a placeholder - it is what keeps "this specialist does not reason yet"
    a valid, testable state rather than a special case, exactly as NullBrain
    does for the orchestrator and NullProvider for model access.
    """

    @property
    def name(self) -> str:
        return "null"

    def consider(self, task: Task, notes: Sequence[str] = ()) -> Insight:
        return Insight.none(
            reason="no reasoning capability is configured", source=self.name
        )
