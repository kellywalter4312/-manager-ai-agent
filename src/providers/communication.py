"""The communication-provider contract - governed phone/messaging access,
never a second reasoning engine.

The same abstraction move `src/providers/live_info.py` already made for
current information, applied to consequential, external communication:
anything that wants to read messages, send one, or place a call depends
on this interface, never on a vendor SDK, a carrier's API shape, or a
specific phone platform.

**Why this is not the Brain, and why the Brain never touches it.** A
Brain decides *what* the user wants ("call Mom") and, through Personality,
what to say about the outcome; it has no method that reaches a phone
network, and nothing under `src/manager/` imports this module. This
module *performs* a deterministic operation - the governed capability
boundary (`src/agents/tools/communication.py`) is what stands between
the two, the same separation `ToolSet` already enforces for every other
tool a specialist might be granted.

**Three operations, never one undifferentiated "do a communication
thing".** The brief's own governance requirement - read-only,
drafting/sending, calling kept separate - maps onto three narrow methods
below, each independently deniable through ordinary `ToolSet` membership
(see `src/agents/tools/communication.py`): a specialist can be granted
``communication_read`` without ever being granted ``communication_send``
or ``communication_call``, the identical all-or-nothing-per-tool
governance every other tool grant in this project already lives under.
No new permission-tiering mechanism was invented for this - three tool
names *are* the tiers.

**Why every result carries provenance and an honest status, never a
fabricated success.** `CommunicationResult`/`CommunicationEvent` answer
"what actually happened, according to what, and when" - the identical
argument `LiveInfoResult`'s own docstring already makes about current
information becoming indistinguishable from a model's own knowledge,
applied here to something with real consequences: a message that was
never sent must never read like one that was.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> datetime:
    """The default clock. Injectable so tests can control time."""
    return datetime.now(timezone.utc)


class CommunicationStatus(str, Enum):
    """How one communication operation ended.

    Three outcomes, the identical distinction ``ToolStatus`` already
    draws for the same reason: a caller reasoning about what to do next
    needs to tell "nothing was attempted" from "an attempt genuinely
    failed" from "it worked".
    """

    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"  # no authorized connection exists yet
    FAILED = "failed"            # a real attempt was made and did not work

    @property
    def ok(self) -> bool:
        return self is CommunicationStatus.SUCCEEDED


@dataclass(frozen=True)
class CommunicationEvent:
    """One message or call already known to have happened - what
    ``read_messages`` returns. Never fabricated: a provider with nothing
    genuine to report returns an empty tuple, not an invented event.
    """

    kind: str  # "message" or "call", a caller-defined label, never interpreted here
    party: str
    summary: str
    occurred_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not str(self.kind).strip():
            raise ValueError("CommunicationEvent.kind must not be empty")
        if not str(self.party).strip():
            raise ValueError("CommunicationEvent.party must not be empty")
        if not str(self.source).strip():
            raise ValueError("CommunicationEvent.source must not be empty")
        object.__setattr__(self, "kind", " ".join(str(self.kind).split()).lower())
        object.__setattr__(self, "party", " ".join(str(self.party).split()))
        object.__setattr__(self, "source", " ".join(str(self.source).split()))
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("CommunicationEvent.occurred_at must be a datetime")


@dataclass(frozen=True)
class CommunicationResult:
    """What actually happened when a send or a call was attempted.

    ``status`` is the honest center of this type: ``UNAVAILABLE`` for
    "no authorized connection exists to do this yet" (the ordinary,
    expected answer today - see ``NullCommunicationProvider``),
    ``FAILED`` for "a real attempt was made and did not succeed", and
    only ``SUCCEEDED`` for a genuine, provider-confirmed outcome. A
    caller must never render this as "done" unless ``status.ok`` is
    True.
    """

    operation: str  # "send" or "call"
    party: str
    status: CommunicationStatus
    detail: str = ""
    source: str = ""
    attempted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.operation).strip():
            raise ValueError("CommunicationResult.operation must not be empty")
        if not str(self.party).strip():
            raise ValueError("CommunicationResult.party must not be empty")
        object.__setattr__(self, "operation", " ".join(str(self.operation).split()).lower())
        object.__setattr__(self, "party", " ".join(str(self.party).split()))
        object.__setattr__(self, "source", " ".join(str(self.source or "").split()))
        if self.attempted_at is not None and not isinstance(self.attempted_at, datetime):
            raise ValueError("CommunicationResult.attempted_at must be a datetime")

    @property
    def ok(self) -> bool:
        return self.status.ok

    def describe(self) -> str:
        """A short line for logs. Excludes ``detail``, which may carry
        message content - the same rule ``MemoryRecord.describe`` and
        ``Contribution.describe`` already follow."""
        return f"{self.operation} to {self.party}: {self.status.value}"


class CommunicationProvider(ABC):
    """A source of governed communication operations.

    Three narrow methods, plain arguments in, a small immutable result
    out - the same shape ``LiveInfoProvider``/``LLMProvider`` already
    proved cheap to implement and cheap to replace. A null
    implementation, a scripted test double, and a real backend all
    satisfy this identically.

    Implementations must never raise for an ordinary, valid input - an
    unauthorized or unconfigured operation is answered with an honestly
    ``UNAVAILABLE`` result (or, for ``read_messages``, an empty tuple),
    never an exception and never a fabricated success. A genuine,
    unexpected failure may still raise; the governed tool boundary
    (``src/agents/tools/communication.py``) catches it exactly as
    ``ToolSet.invoke`` already catches every other tool's exceptions.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "null"."""

    @abstractmethod
    def read_messages(self, *, limit: int = 10) -> tuple[CommunicationEvent, ...]:
        """Recent messages and calls already known to this provider -
        read-only, never a send or a call. An honestly empty tuple when
        nothing is known, never an invented event."""

    @abstractmethod
    def send_message(self, *, to: str, content: str) -> CommunicationResult:
        """Attempt to send ``content`` to ``to``. Consequential - see
        this module's own docstring on governance."""

    @abstractmethod
    def place_call(self, *, to: str) -> CommunicationResult:
        """Attempt to place a call to ``to``. Consequential - see this
        module's own docstring on governance."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
