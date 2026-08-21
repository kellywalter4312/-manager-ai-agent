"""The Manager's reasoning capability.

The orchestrator already has two decision-making collaborators, and this is
deliberately neither of them:

    Router    - which agent can do this?      (mechanical capability matching)
    Assessor  - is this result well-formed?   (contract validation)
    Brain     - what should happen here, and why?

Router and Assessor answer narrow, checkable questions with rules. A brain is
for the judgement that rules cannot express: reading an ambiguous situation,
weighing options, proposing what to do next.

Two boundaries keep this from swallowing the Manager:

  - **The interface is one method.** A brain deliberates and returns a
    decision. It does not execute, delegate, register, fetch or report - those
    belong to the orchestrator, which remains in charge.
  - **A decision is advice, not an instruction.** The Manager consults the
    brain; it is not obliged to obey it. Nothing here can override a routing
    decision or an assessment.

Deliberately absent: memory. A brain may one day be given past context to
reason over, but storing it is a separate component with a separate lifecycle.
This module holds no state between calls.

Also absent: any model. The abstraction is provider-agnostic, so a brain backed
by a language model, by rules, or by nothing at all satisfies the same
contract. NullBrain is the default and needs no key, no network and no model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from src.core.contracts import AgentSpec, Task


class BrainError(Exception):
    """Raised when a decision cannot be formed into a valid contract."""


class BrainAction(str, Enum):
    """What the brain suggests the orchestrator does next."""

    HANDLE_SELF = "handle_self"  # the Manager should do this itself
    DELEGATE = "delegate"        # a specialist is better suited
    REPORT = "report"            # there is something worth telling the user
    ESCALATE = "escalate"        # a human needs to decide
    UNKNOWN = "unknown"          # the brain has no useful view

    @property
    def label(self) -> str:
        return {
            "handle_self": "HANDLE IT MYSELF",
            "delegate": "DELEGATE",
            "report": "REPORT TO THE USER",
            "escalate": "ESCALATE - needs a human",
            "unknown": "NO VIEW",
        }[self.value]

    @property
    def is_decided(self) -> bool:
        return self is not BrainAction.UNKNOWN


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = [" ".join(str(v).split()) for v in values or ()]
    return tuple(dict.fromkeys(v for v in cleaned if v))


@dataclass(frozen=True)
class BrainDecision:
    """One piece of advice from the brain.

    Frozen, like every other contract here: a decision records what was
    concluded at a moment, and the orchestrator acting on it must not be able
    to edit it afterwards.
    """

    action: BrainAction
    reason: str
    confidence: float = 0.0
    delegate_to: str | None = None
    steps: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.action, str) and not isinstance(self.action, BrainAction):
            try:
                object.__setattr__(self, "action", BrainAction(self.action.strip().lower()))
            except ValueError:
                raise BrainError(f"Unknown action {self.action!r}") from None

        # The same honesty rule the rest of the project applies: advice the
        # user or the orchestrator cannot evaluate is not worth acting on.
        if not str(self.reason).strip():
            raise BrainError("BrainDecision must explain its reasoning")
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))

        if not 0.0 <= float(self.confidence) <= 1.0:
            raise BrainError(
                f"BrainDecision.confidence must be between 0 and 1, got {self.confidence}"
            )
        object.__setattr__(self, "confidence", float(self.confidence))

        target = (self.delegate_to or "").strip()
        object.__setattr__(self, "delegate_to", target or None)

        # Advising delegation without naming anyone is not actionable advice.
        if self.action is BrainAction.DELEGATE and not self.delegate_to:
            raise BrainError("a DELEGATE decision must name an agent")

        object.__setattr__(self, "steps", _clean(self.steps))
        object.__setattr__(self, "source", str(self.source).strip())

    @property
    def is_decided(self) -> bool:
        return self.action.is_decided

    def describe(self) -> str:
        """A short line for logs."""
        target = f" -> {self.delegate_to}" if self.delegate_to else ""
        via = f" via={self.source}" if self.source else ""
        return (
            f"{self.action.value}{target} confidence={self.confidence:.2f}"
            f"{via} steps={len(self.steps)}"
        )

    @classmethod
    def no_view(cls, reason: str, source: str = "") -> "BrainDecision":
        """A valid decision meaning "I have nothing useful to add"."""
        return cls(
            action=BrainAction.UNKNOWN,
            reason=reason,
            confidence=0.0,
            source=source,
        )


class ManagerBrain(ABC):
    """A reasoning capability the orchestrator can consult."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used for provenance and logs."""

    @abstractmethod
    def deliberate(
        self,
        task: Task,
        agents: Sequence[AgentSpec] = (),
        notes: Sequence[str] = (),
    ) -> BrainDecision:
        """Consider a situation and advise what should happen next.

        ``agents`` describes who is available, using the spec each agent
        already publishes - so a brain can reason about delegation without
        depending on BaseAgent or reaching into the registry itself.

        ``notes`` is free context the caller wants considered.

        Implementations should return a decision rather than raise. The
        Manager guards this boundary anyway, because a brain is injected and
        may be anything.
        """


class NullBrain(ManagerBrain):
    """The default: no reasoning capability configured.

    Not a placeholder to be replaced later - it is what keeps "the Manager has
    no brain" a valid, testable state rather than a special case. Same role
    NullProvider plays one layer down.
    """

    @property
    def name(self) -> str:
        return "null"

    def deliberate(
        self,
        task: Task,
        agents: Sequence[AgentSpec] = (),
        notes: Sequence[str] = (),
    ) -> BrainDecision:
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )
