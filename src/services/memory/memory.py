"""The memory contract: what an agent knows, and how it gets it back.

Memory is the second half of a pair, and the two halves are deliberately
different abstractions:

    Brain   - reasoning, judgement, deciding what should happen
    Memory  - retaining information and handing it back when asked

Keeping them apart is the point. A brain that also stored things would decide
what is worth keeping, and a memory that also reasoned would decide what a
question means - each would then have two reasons to change, and neither could
be swapped without dragging the other along. Separated, a reasoning upgrade and
a storage upgrade are independent changes.

**This contract does no reasoning.** Notice what is absent: nothing here
summarises, ranks by importance, decides what matters, forgets on its own, or
interprets a question. ``recall`` performs literal token overlap and returns
what it finds. Judgement is the brain's job, and a memory that quietly decided
what to keep would be making decisions nobody could inspect.

**Nothing here persists.** The only implementation shipping with this contract
holds records in a list that dies with the process. A durable backend is a
later, deliberate decision - see EphemeralMemory - and writing to disk or a
database is never a side effect of remembering something.

Storage-agnostic and domain-agnostic by construction: this module imports one
thing from the project (``tokenize``, which is pure text handling) and nothing
else. No provider, no vendor, no mailbox, no Gmail, no specialist, no database
driver. An implementation that needed any of those would be importing them
itself, which the tests check for.

Scope note on identity: a record carries no "owner" field, and that is a design
decision rather than an oversight. The *instance* is the boundary - the Manager
holds one Memory, each specialist holds its own, and they cannot see each
other's records because they are different objects. An owner field would invite
exactly the global shared store this architecture is trying to avoid.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.core.contracts import tokenize


class MemoryStoreError(Exception):
    """Raised when a memory cannot satisfy a request.

    Named MemoryStoreError rather than the more obvious MemoryError because
    that name is taken by a Python builtin - shadowing it would mean a caller
    writing ``except MemoryError`` silently caught the wrong thing, or missed
    a real out-of-memory condition. Same role as MailboxError and BrainError.
    """


def normalise_tags(values: Iterable[str]) -> frozenset[str]:
    """Trim, lower-case and drop blanks, so tag matching is predictable.

    The same normalisation AgentSpec applies to capabilities, and for the same
    reason: how a caller happened to capitalise a tag should never decide
    whether a record can be found again.
    """
    return frozenset(
        cleaned
        for cleaned in (" ".join(str(v).split()).lower() for v in values or ())
        if cleaned
    )


def utc_now() -> datetime:
    """The default clock. Injectable so tests can control time."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MemoryRecord:
    """One thing an agent knows.

    Frozen, like every other contract in this project: a record is an account
    of what was true when it was written, and nothing that later reads it
    should be able to edit it in place. Correcting a memory means storing a new
    record, which leaves the correction visible.

    ``content`` is the only required field. Everything else exists to make a
    record findable again without the contract having to understand what the
    record means:

        kind     - a caller-defined category ("preference", "outcome", "fact")
        tags     - caller-defined labels, normalised for reliable matching
        context  - arbitrary structured detail, carried but never interpreted
        source   - where this record came from ("user_stated", "gmail",
                   "milestone", ...), for a caller that wants to say plainly
                   why something is remembered rather than burying that fact
                   as one more ad hoc key inside the opaque ``context`` bag.
                   Optional, and empty by default: existing callers that have
                   always used ``context["source"]`` keep working unchanged -
                   this is a place to put it, never a requirement to.

    ``context`` is deliberately opaque. Memory stores it and hands it back; it
    never reads inside it. That is what keeps this contract free of any
    knowledge of what a particular agent chooses to remember.
    """

    content: str
    kind: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    context: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = ""

    def __post_init__(self) -> None:
        # A memory of nothing is not a memory. Storing blanks would quietly
        # fill a store with records that can never be recalled or acted on.
        if not str(self.content).strip():
            raise MemoryStoreError("MemoryRecord.content must not be empty")
        object.__setattr__(self, "content", " ".join(str(self.content).split()))

        object.__setattr__(self, "kind", " ".join(str(self.kind).split()).lower())
        object.__setattr__(self, "tags", normalise_tags(self.tags))
        object.__setattr__(self, "source", " ".join(str(self.source).split()))

        if not str(self.id).strip():
            raise MemoryStoreError("MemoryRecord.id must not be empty")
        object.__setattr__(self, "id", str(self.id).strip())

        if not isinstance(self.created_at, datetime):
            raise MemoryStoreError(
                f"MemoryRecord.created_at must be a datetime, "
                f"got {type(self.created_at).__name__}"
            )

    @property
    def tokens(self) -> frozenset[str]:
        """The content as word tokens, used for literal recall matching.

        Reuses core.tokenize - the same function that matches a task against an
        agent's capabilities. Recall is plain token overlap: no embeddings, no
        semantic search, no model. Those are later implementation choices, and
        a contract that assumed them would have foreclosed the simple one.
        """
        return tokenize(self.content)

    def matches(self, tokens: frozenset[str]) -> int:
        """How many query tokens appear in this record. Zero means no match."""
        return len(self.tokens & tokens)

    def describe(self) -> str:
        """A short line for logs.

        Excludes ``content`` and ``context``. Memory holds whatever an agent was
        told - preferences, personal details, message-derived facts - and this
        project keeps that out of log files. The same rule Completion.describe()
        and LLMJudgement.describe() follow.
        """
        kind = f" kind={self.kind}" if self.kind else ""
        return f"record {self.id}{kind} tags={len(self.tags)} chars={len(self.content)}"


class Memory(ABC):
    """What an agent retains, and how it gets it back.

    Four methods, chosen to be the smallest set that is actually usable:

        remember  - store a record
        recall    - retrieve records that match
        forget    - remove one record
        clear     - remove everything

    ``forget`` and ``clear`` are not conveniences. Anything that retains
    information about a person must offer a way to remove it, and a store that
    could only grow would have no answer to "delete that". Making removal part
    of the contract means every future backend has to implement it, rather than
    it being a feature the persistent one forgets to add.

    Implementations must be **deterministic**: the same records and the same
    query produce the same result in the same order, every time. Recall that
    varied run to run would make every consumer untestable.
    """

    @abstractmethod
    def remember(self, record: MemoryRecord) -> MemoryRecord:
        """Store a record and return it.

        Takes a fully-formed record rather than loose arguments, so the value
        object is the contract and every implementation stores exactly the same
        shape. Returning it lets a caller chain without re-reading.

        Implementations must raise MemoryStoreError - not a backend-specific
        exception - when storing fails, so callers depend on this module rather
        than on whatever database happens to sit behind it.
        """

    @abstractmethod
    def recall(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        """Return up to ``limit`` matching records, best match first.

        Every filter is optional, and they combine:

            query  - literal token overlap against record content
            kind   - exact category match
            tags   - a record must carry *all* of the requested tags

        With no filters at all, returns the most recent records. That is the
        useful default for "what do I know?", and it means an empty query is a
        valid question rather than an error.

        Returns a tuple, not an iterator: the caller always wants a bounded,
        re-readable result, and ``limit`` keeps it bounded. Same reasoning as
        Mailbox.fetch returning a list.

        Never raises on finding nothing - an empty tuple is the honest answer
        to "I know nothing about that", and it is not an error.
        """

    @abstractmethod
    def forget(self, record_id: str) -> bool:
        """Remove one record. True if it existed, False if it did not.

        Returning a bool rather than raising makes "delete this, whether or not
        it is there" a single call, which is what callers almost always want.
        """

    @abstractmethod
    def clear(self) -> int:
        """Remove every record. Returns how many were removed."""

    def __len__(self) -> int:
        """How many records are held. Implementations should override."""
        return len(self.recall(limit=2**31 - 1))
