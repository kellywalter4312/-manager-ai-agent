"""The room several agents can be in without becoming one agent.

The problem this solves, stated once:

    Alfred and a specialist need to work on the same thing.
    They must not end up thinking with the same brain.

The tempting solution is to give each agent a way to reach the others - hand a
specialist the Manager, or the registry, or its peers. That solves
collaboration and destroys isolation in the same line, because an agent holding
the registry can reach every other agent's brain, memory and tools. Every
guarantee in `SpecialistContext` about what a specialist is *not* given would
be undone by the one object that was.

So collaboration happens the way it happens between people:

    agents do not get each other.
    agents get put in the same room.

**A Conversation holds identities, never agents.** A Participant is a name and
a kind. There is no brain here, no memory, no toolset, no registry, no Manager
and no agent object of any sort - so nothing can be reached *through* the room
that could not be reached without it. The room knows who is in it. It does not
hand you the people.

That single rule is what makes the shared thing context rather than
intelligence:

                     CONVERSATION CONTEXT
                             |
              +--------------+--------------+
              |              |              |
           ALFRED        SPECIALIST     SPECIALIST
            Brain           Brain          Brain      <- never merged
            Memory          Memory         Memory     <- never shared

**No participant ceiling.** There is deliberately no maximum. Not three, not
five, not ten. A limit written here would be an architectural claim about how
many agents may think about a problem together, and there is no honest number
to write. What actually bounds a conversation is the cost of running the agents
in it, which is a runtime concern and not this module's to guess at.

**What this module does not do,** because each belongs to something that
already exists:

    routing        who should answer  -> the Manager, through the router
    reasoning      what to answer     -> each agent's own brain
    retention      what to keep       -> each agent's own memory and policy
    presentation   how to show it     -> the Control Center

A Conversation records what was said, by whom, to whom, and when. Everything
else is somebody else's job, and a room that started doing any of it would have
become a second orchestrator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Iterator


class ConversationError(Exception):
    """Raised when a conversation is asked for something it cannot do."""


def utc_now() -> datetime:
    """The default clock, matching MemoryRecord's. Injectable for tests."""
    return datetime.now(timezone.utc)


class ParticipantKind(str, Enum):
    """What sort of thing is taking part.

    Two kinds, because exactly two things speak: the person the system serves,
    and agents. The distinction is not decoration - it is what lets a later
    voice layer know whose turn to listen for, and what stops a transcript
    presenting a person's words as an agent's.
    """

    PERSON = "person"
    AGENT = "agent"

    @property
    def is_agent(self) -> bool:
        return self is ParticipantKind.AGENT


@dataclass(frozen=True)
class Participant:
    """Somebody in the room. A name and a kind, and deliberately nothing else.

    **This is the isolation boundary.** There is no ``agent`` field, no brain,
    no memory, no toolset and no registry - not "one that is ignored", but no
    field for any of them to live in. Making them absent rather than unused is
    what turns "agents must not reach each other through the room" from a rule
    somebody has to remember into something the type cannot express.

    A future specialist joins by being named. Nothing here learns it exists,
    and nothing here needs editing when it does.
    """

    name: str
    kind: ParticipantKind = ParticipantKind.AGENT

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ConversationError("a participant must have a name")
        object.__setattr__(self, "name", " ".join(str(self.name).split()))

    @property
    def key(self) -> str:
        """The name as it is matched. Case is how somebody typed it, not who
        they are, so it never decides whether a participant is found."""
        return self.name.lower()

    def describe(self) -> str:
        return f"{self.name} ({self.kind.value})"


@dataclass(frozen=True)
class Utterance:
    """One thing that was said, and what is known about the saying of it.

    Frozen, like every other record in this project: a transcript is an account
    of what happened, and nothing reading it later should be able to edit it.
    Correcting the record means saying something else, which leaves the
    correction visible.

    ``addressed`` is who it was aimed at, when anybody was. This is the seam a
    voice layer will route on - "Alfred, bring Research in" is addressed to
    Alfred - and it is recorded rather than acted on, because deciding who
    answers belongs to the Manager.

    ``detail`` is opaque payload, carried and never interpreted. The same
    contract ``MemoryRecord.context`` has, and for the same reason: it lets a
    caller attach the trail of how a turn was handled without this module
    learning what a trail is, or importing the layer that owns one.
    """

    speaker: str
    text: str
    when: datetime = field(default_factory=utc_now)
    addressed: str | None = None
    detail: Any | None = None

    def __post_init__(self) -> None:
        if not str(self.speaker).strip():
            raise ConversationError("an utterance must have a speaker")
        if not str(self.text).strip():
            raise ConversationError("an utterance must say something")
        object.__setattr__(self, "speaker", " ".join(str(self.speaker).split()))
        object.__setattr__(self, "text", str(self.text).strip())
        if self.addressed is not None:
            object.__setattr__(self, "addressed", " ".join(str(self.addressed).split()))

    def describe(self) -> str:
        """A short line for a log. Excludes ``text``.

        A conversation carries whatever a person chose to say, and this project
        keeps that out of log files - the same rule MemoryRecord.describe and
        Completion.describe follow.
        """
        to = f" -> {self.addressed}" if self.addressed else ""
        return f"{self.speaker}{to}: {len(self.text)} chars"


class Conversation:
    """Who is in the room, and what has been said in it.

    Mutable, holding frozen records - the same shape as EventLog and Memory. A
    conversation is a living thing that accumulates; what it accumulates is
    immutable once said.

    Unbounded, deliberately. EventLog is capped because it is an observability
    aid and losing the oldest tick costs nothing; a conversation *is* the
    record of the conversation, and silently discarding what somebody said
    would be the room lying about itself. Compaction is real and is a *memory*
    concern - it arrives with session memory, which decides what a long
    conversation is worth keeping. It does not belong to the room.
    """

    def __init__(
        self,
        participants: Iterable[Participant] = (),
        *,
        id: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.id = id or uuid.uuid4().hex[:12]
        self.started_at = started_at or utc_now()
        self._participants: list[Participant] = []
        self._utterances: list[Utterance] = []
        for participant in participants:
            self.join(participant)

    # ------------------------------------------------------------------
    # Who is here
    # ------------------------------------------------------------------

    @property
    def participants(self) -> tuple[Participant, ...]:
        return tuple(self._participants)

    @property
    def agents(self) -> tuple[Participant, ...]:
        """Only the agents. Useful to a screen; decides nothing."""
        return tuple(p for p in self._participants if p.kind.is_agent)

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
        """Bring somebody into the room.

        Idempotent by name: joining twice is not an error and does not produce
        a second entry. "Bring Research in" said twice is a person repeating
        themselves, not a request for two Researches.

        There is no capacity check, and adding one later would need a reason
        this module does not have.
        """
        if not isinstance(participant, Participant):
            raise ConversationError(
                "only a Participant may join - a conversation holds identities, "
                "never agents, so that nothing can reach an agent through the room"
            )
        existing = self.participant(participant.name)
        if existing is not None:
            return existing
        self._participants.append(participant)
        return participant

    def leave(self, name: str) -> bool:
        """Remove somebody. True if they were here, False if they were not.

        What they said stays. A transcript with a speaker silently deleted from
        it would be a record of a conversation that never happened.
        """
        participant = self.participant(name)
        if participant is None:
            return False
        self._participants.remove(participant)
        return True

    # ------------------------------------------------------------------
    # What was said
    # ------------------------------------------------------------------

    @property
    def utterances(self) -> tuple[Utterance, ...]:
        return tuple(self._utterances)

    def say(self, utterance: Utterance) -> Utterance:
        """Record one thing that was said.

        The speaker must be in the room. Not ceremony: an utterance from
        somebody who never joined is either a bug or a participant who was
        never announced, and both are worth failing on rather than quietly
        producing a transcript with a stranger in it.
        """
        if not self.has(utterance.speaker):
            raise ConversationError(
                f"'{utterance.speaker}' is not in this conversation. "
                "Join first - a transcript must not contain somebody who was "
                "never here."
            )
        self._utterances.append(utterance)
        return utterance

    def said_by(self, name: str) -> tuple[Utterance, ...]:
        key = " ".join(str(name).split()).lower()
        return tuple(u for u in self._utterances if u.speaker.lower() == key)

    def latest(self, limit: int = 10) -> tuple[Utterance, ...]:
        """The most recent utterances, oldest first within the window."""
        if limit <= 0:
            raise ConversationError("limit must be positive")
        return tuple(self._utterances[-limit:])

    # ------------------------------------------------------------------
    # Who was being spoken to
    # ------------------------------------------------------------------

    def addressed_in(self, text: str) -> str | None:
        """Which participant this text is aimed at, if any.

        Deterministic and model-free, so it works identically in Half Brain.
        Addressing is a fact about the words and the roster, not a judgement.

        Conservative on purpose: a name counts as an address only when it
        *opens* the utterance. "Alfred, bring Research in" addresses Alfred;
        "tell Alfred about this" addresses nobody, because the person is
        talking about Alfred rather than to him. Guessing the second case would
        make the room route on a hunch.

        Returns the participant's own spelling, never the caller's, so a later
        router can match on it exactly. Returns None when nobody is addressed -
        an honest answer, and the common one.

        Richer addressing - wake words, "hey <name>", an agent addressed
        mid-sentence - belongs to the voice layer that will need it. This is
        the seam it attaches to, not the whole mechanism.
        """
        stripped = str(text or "").strip().lower()
        if not stripped:
            return None

        best: Participant | None = None
        for participant in self._participants:
            key = participant.key
            if not stripped.startswith(key):
                continue
            rest = stripped[len(key):]
            # A name must end the utterance or be followed by punctuation or a
            # space - otherwise "Ali" would match inside "Alison".
            if rest and rest[0].isalnum():
                continue
            # Longest match wins, so "Research Agent" beats "Research".
            if best is None or len(key) > len(best.key):
                best = participant
        return best.name if best is not None else None

    # ------------------------------------------------------------------
    # Reading the room
    # ------------------------------------------------------------------

    def transcript(self) -> tuple[str, ...]:
        """What was said, in order, as plain lines.

        Formatting for a caller's convenience, never a decision about what a
        screen should look like. The Control Center owns presentation.
        """
        return tuple(f"{u.speaker}: {u.text}" for u in self._utterances)

    def describe(self) -> str:
        """One line for a status screen. Counts, never content."""
        return (
            f"conversation {self.id}: {len(self._participants)} participant(s), "
            f"{len(self._utterances)} utterance(s)"
        )

    def __len__(self) -> int:
        return len(self._utterances)

    def __iter__(self) -> Iterator[Utterance]:
        return iter(self._utterances)

    def __repr__(self) -> str:
        return f"<Conversation {self.id} participants={len(self._participants)}>"
