"""The Shared Context Graph - governed context exchange, never a second Memory.

The problem this solves, stated the way conversation.py states its own:

    Alfred and a specialist need to work on the same task.
    What each contributes must be visible to the others.
    Neither's own Memory may be merged, copied, or reached into to get it.

Conversation.py already solved "several agents in one room without becoming
one agent" for an open-ended chat transcript. This module solves a narrower,
related problem: one bounded piece of *work*, with participants who
deliberately publish what they found, each contribution attributed to
whoever produced it. It is not a rename of Conversation and it does not
replace it - a Collaboration is scoped to one task and closes when the task
is done; a Conversation is the ongoing room the people and agents are in.
The two may exist side by side, correlated by whatever composes them, and
neither imports the other.

**A Collaboration holds identities and contributions, never agents.**
Exactly the same isolation rule Participant already enforces: no field here
can hold a Brain, a Memory, a toolset or a registry, so nothing can be
reached *through* a collaboration that could not be reached without one.

                        SHARED CONTEXT GRAPH
                        (one Collaboration, one task)
                                |
                +---------------+---------------+
                |               |               |
             ALFRED        SPECIALIST       SPECIALIST
       contributes       contributes       contributes
       (Contribution)    (Contribution)    (Contribution)
                |               |               |
             Brain            Brain           Brain      <- never merged
             Memory           Memory          Memory     <- never shared,
                                                             never read from
                                                             automatically

**Nothing here is retained.** A Collaboration lives as long as its caller
keeps a reference to it and is not written to disk by this module. If an
agent's own MemoryPolicy later decides a contribution was worth keeping
long-term, that agent retains it through its own governed write path
(ManagerAgent.retain, or a specialist's own remember_experience) - exactly
as it would anything else it learned. This module has no opinion about that
and no way to cause it.

**Contribution is explicit, never automatic.** There is no method here that
reads an agent's Memory, Brain state, or conversation history and turns it
into a contribution on its own. A contribution exists because a caller
built one and handed it over - the same discipline Conversation.say and
MemoryPolicy.evaluate already require, applied here to the third place this
project could have quietly let information leak sideways.

**No agent is named here.** Nothing in this module has ever heard of Alfred
or Arthur. A future specialist collaborates by being named a participant
and contributing like anyone else.

**This module holds no orchestrator.** Like every other file under
``src/conversation/``, it imports nothing from ``src.manager``,
``src.agents``, ``src.registry``, ``src.services.memory``,
``src.composition`` or ``src.providers`` - see
``tests/test_conversation.py``'s own package-wide scan, which this module
is bound by exactly as ``conversation.py`` already is. The orchestration
helper that actually starts a collaboration against a real
``ManagerAgent`` (``start_collaboration``) therefore lives beside
``scripts/exchange.py``'s own ``exchange()`` - the established home for
"tie the room to the orchestrator" glue - not here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from src.conversation.conversation import Participant, ParticipantKind


class CollaborationError(Exception):
    """Raised when a collaboration is asked for something it cannot do."""


def utc_now() -> datetime:
    """The default clock, matching Conversation's and MemoryRecord's.
    Injectable for tests."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Contribution:
    """One deliberate piece of context offered to a collaboration.

    Frozen, like every other record in this project: a contribution is an
    account of what was offered, and nothing reading it later should be
    able to edit it in place.

    ``source`` is provenance - who contributed it, required, and matched
    against the collaboration's own participants (see
    ``Collaboration.contribute``) so it can never name somebody who was
    never invited. ``kind`` is a caller-defined category ("result",
    "fact", "question") for the same reason ``MemoryRecord.kind`` and
    ``Utterance``'s own shape already use one - findable without this
    module having to understand what it means. ``relates_to`` names other
    contributions' ids this one responds to or builds on; carried and
    never interpreted, the same contract ``MemoryRecord.context`` and
    ``Utterance.detail`` already have.
    """

    source: str
    content: str
    kind: str = ""
    relates_to: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise CollaborationError("a contribution must name its source")
        if not str(self.content).strip():
            raise CollaborationError("a contribution must contain something")
        object.__setattr__(self, "source", " ".join(str(self.source).split()))
        object.__setattr__(self, "content", str(self.content).strip())
        object.__setattr__(self, "kind", " ".join(str(self.kind).split()).lower())
        object.__setattr__(self, "relates_to", tuple(self.relates_to))
        if not str(self.id).strip():
            raise CollaborationError("Contribution.id must not be empty")

    def describe(self) -> str:
        """A short line for a log. Excludes ``content``, the same rule
        MemoryRecord.describe and Utterance.describe already follow -
        a contribution carries whatever an agent found, and that stays
        out of log files."""
        kind = f" kind={self.kind}" if self.kind else ""
        return f"contribution {self.id} from {self.source}{kind} chars={len(self.content)}"


class Collaboration:
    """A governed, bounded space where participants exchange context about
    one piece of work - the Shared Context Graph.

    Scoped to a single ``task``, unlike Conversation's open-ended room.
    Mutable, holding frozen records - the same shape Conversation and
    Memory already use. Closing stops further participants and
    contributions; reading remains available afterward, because the
    point of closing a collaboration is to let whoever asked for it read
    a settled answer, not to make the answer disappear.
    """

    def __init__(
        self,
        task: str,
        participants: Iterable[Participant] = (),
        *,
        id: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        if not str(task).strip():
            raise CollaborationError(
                "a collaboration must describe the task it exists for"
            )
        self.id = id or uuid.uuid4().hex[:12]
        self.task = " ".join(str(task).split())
        self.started_at = started_at or utc_now()
        self._closed_at: datetime | None = None
        self._participants: list[Participant] = []
        self._contributions: list[Contribution] = []
        for participant in participants:
            self.join(participant)

    @classmethod
    def restore(
        cls,
        task: str,
        participants: Iterable[Participant],
        contributions: Iterable[Contribution],
        *,
        id: str,
        started_at: datetime,
        closed_at: datetime | None = None,
    ) -> "Collaboration":
        """A collaboration rebuilt from a saved record of one. Every
        contribution must still name one of the restored participants - a
        saved record is held to the same provenance rule ``contribute``
        applies, so a record edited to name a stranger is refused."""
        collaboration = cls(task, participants, id=id, started_at=started_at)
        for contribution in contributions:
            collaboration.contribute(contribution)
        collaboration._closed_at = closed_at
        return collaboration

    # ------------------------------------------------------------------
    # Who is here
    # ------------------------------------------------------------------

    @property
    def participants(self) -> tuple[Participant, ...]:
        return tuple(self._participants)

    def has(self, name: str) -> bool:
        key = " ".join(str(name).split()).lower()
        return any(p.key == key for p in self._participants)

    def participant(self, name: str) -> Participant | None:
        key = " ".join(str(name).split()).lower()
        for participant in self._participants:
            if participant.key == key:
                return participant
        return None

    def join(self, participant: Participant) -> Participant:
        """Add a participant. Idempotent by name, the same rule
        Conversation.join already follows and for the same reason:
        inviting somebody twice is not a request for two of them.
        """
        if self.closed:
            raise CollaborationError("cannot join a closed collaboration")
        if not isinstance(participant, Participant):
            raise CollaborationError(
                "only a Participant may join - a collaboration holds identities, "
                "never agents, so nothing can reach an agent through it"
            )
        existing = self.participant(participant.name)
        if existing is not None:
            return existing
        self._participants.append(participant)
        return participant

    # ------------------------------------------------------------------
    # What was contributed
    # ------------------------------------------------------------------

    @property
    def contributions(self) -> tuple[Contribution, ...]:
        return tuple(self._contributions)

    def contribute(self, contribution: Contribution) -> Contribution:
        """Publish one contribution.

        The source must already be a participant - the same "you must be
        in the room to speak in it" rule Conversation.say enforces, for
        the same reason: provenance that could name somebody who was
        never invited is provenance nobody could trust.
        """
        if self.closed:
            raise CollaborationError("cannot contribute to a closed collaboration")
        if not isinstance(contribution, Contribution):
            raise CollaborationError(
                f"only a Contribution may be published, got {type(contribution).__name__}"
            )
        if not self.has(contribution.source):
            raise CollaborationError(
                f"'{contribution.source}' is not a participant in this collaboration - "
                "join first, the same rule a room already applies to speaking in it"
            )
        self._contributions.append(contribution)
        return contribution

    def context_for(
        self, participant_name: str, *, kind: str | None = None, limit: int = 50
    ) -> tuple[Contribution, ...]:
        """What ``participant_name`` may read from this collaboration.

        Only a participant may ask - the same membership check
        ``contribute`` applies to speaking, applied here to reading, so a
        name that was never invited cannot read what was shared either.
        Every contribution is visible to every participant once
        published: a collaboration is deliberately a shared thing, and
        ``kind`` narrows what comes back without this module ranking
        anything by relevance - it has no model to do that with, and
        inventing a heuristic would be exactly the reasoning this layer
        is not supposed to do (see the module docstring).
        """
        if not self.has(participant_name):
            raise CollaborationError(
                f"'{participant_name}' is not a participant in this collaboration"
            )
        if limit <= 0:
            raise CollaborationError("limit must be positive")

        results = tuple(self._contributions)
        if kind is not None:
            wanted = " ".join(str(kind).split()).lower()
            results = tuple(c for c in results if c.kind == wanted)
        return results[-limit:]

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed_at is not None

    @property
    def closed_at(self) -> datetime | None:
        return self._closed_at

    def close(self) -> None:
        """Mark this collaboration complete. Idempotent - closing twice
        keeps the original close time rather than overwriting it."""
        if self._closed_at is None:
            self._closed_at = utc_now()

    # ------------------------------------------------------------------
    # Reading the graph
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """One line for a status screen. Counts, never content - the
        same rule Conversation.describe and MemoryRecord.describe
        already follow."""
        state = "closed" if self.closed else "open"
        return (
            f"collaboration {self.id} ({state}) on '{self.task}': "
            f"{len(self._participants)} participant(s), "
            f"{len(self._contributions)} contribution(s)"
        )

    def __len__(self) -> int:
        return len(self._contributions)

    def __repr__(self) -> str:
        return f"<Collaboration {self.id} task={self.task!r} contributions={len(self._contributions)}>"
