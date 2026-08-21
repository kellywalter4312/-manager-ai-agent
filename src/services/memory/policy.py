"""Whether a record is allowed into a memory.

The Memory contract answers "how is this stored and retrieved?". It has no
opinion about *what* is worth storing, and deliberately so - a store that
decided what to keep would be making judgements nobody could inspect.

This is where that opinion lives, as a separate, replaceable dependency:

    Memory        - retention and retrieval
    MemoryPolicy  - admission: may this be remembered at all?

Splitting them means the answer to "what does this agent remember?" is one
readable object rather than a rule buried in whatever code happened to call
remember(). It also means each agent can hold a different policy while sharing
the same storage implementation - the Manager keeps orchestration knowledge, a
specialist will keep its own domain knowledge, and neither has to be taught
about the other's rules.

Note what a policy does *not* do: it never stores anything, never reads a
store, and never edits a record. It looks at one record and returns a verdict.
Enforcement is the caller's, which keeps "decide" and "act" separable and
makes every rule testable without a memory at all.

Domain-agnostic by construction: this module imports one thing from the
project - the record type it judges - and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from src.services.memory.memory import MemoryRecord


@dataclass(frozen=True)
class PolicyVerdict:
    """The answer to "may this be remembered?", with its reason.

    A bare bool would be cheaper and worse. A rejected record is exactly the
    case where someone needs to know *why* - was the category wrong, did it
    look like a secret, was it too long? - and a reason that has to be
    reconstructed from the calling code is a reason nobody will read.

    Frozen, like every other verdict in this project: a decision is a record of
    what was concluded, not a mutable scratchpad.
    """

    allowed: bool
    reason: str
    category: str | None = None

    def __post_init__(self) -> None:
        # An admission decision that cannot explain itself is not reviewable,
        # and an unreviewable rule about what an agent retains is worse than no
        # rule at all.
        if not str(self.reason).strip():
            raise ValueError("PolicyVerdict must explain itself")
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))

        category = (self.category or "").strip().lower()
        object.__setattr__(self, "category", category or None)

    def __bool__(self) -> bool:
        """So ``if policy.evaluate(record):`` reads the obvious way."""
        return self.allowed

    def describe(self) -> str:
        """A short line for logs.

        Excludes the record entirely - the reason may quote a category, never
        content. A rejected secret must not be written to a log file by the
        very rule that refused to store it.
        """
        outcome = "allowed" if self.allowed else "refused"
        category = f" category={self.category}" if self.category else ""
        return f"{outcome}{category}: {self.reason}"


class MemoryPolicy(ABC):
    """Decides whether a record may enter a memory.

    One method. A policy that could also store, retrieve or rewrite would be a
    second memory implementation wearing a different name.
    """

    @abstractmethod
    def evaluate(self, record: MemoryRecord) -> PolicyVerdict:
        """Judge one record.

        Must return a verdict rather than raise. A record that fails a rule is
        an ordinary, expected outcome - not an error condition - and callers
        should be able to filter a batch without wrapping every item in a
        try/except.
        """

    def permits(self, record: MemoryRecord) -> bool:
        """Whether ``record`` may be remembered. Convenience over evaluate()."""
        return self.evaluate(record).allowed

    def admissible(self, records: Iterable[MemoryRecord]) -> tuple[MemoryRecord, ...]:
        """The subset of ``records`` this policy allows, in the given order.

        Filtering only - nothing is stored. The caller decides what to do with
        what comes back, which is what keeps deciding and acting separable.
        """
        return tuple(record for record in records if self.permits(record))
