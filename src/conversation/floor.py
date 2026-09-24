"""Who holds the floor in a conversation, and who speaks next.

The problem this solves:

    Several agents are discussing one thing with a person.
    Each must speak in turn, and only in turn.
    Nobody who is not in the room may take a turn at all.

**Order is data, never a rule written here.** Whoever opens a round hands the
floor an order - the speakers it wants to hear, in the sequence it wants them.
This module keeps no order of its own: not "the orchestrator first", not "the
specialists alphabetically", not any count. A round of one and a round of forty
are the same code, and a participant who joins the room later can be given a
turn in the next round without anything here changing.

**Only somebody in the room may hold the floor,** checked against the
conversation itself when the round opens and again when each turn is taken. A
participant who has left by the time their turn comes is skipped, and the skip
is recorded rather than silently absorbed - a transcript of a round should say
that somebody did not speak, not pretend they were never due to.

**A person is never queued behind the agents.** The floor tracks turns for
whoever it was given, and a person may be given turns like anyone else; but a
person may also *interject* at any moment, out of turn, without consuming or
cancelling anyone's place. What an interjection means for the discussion is the
orchestrator's business - the floor only records that it happened, and who it
interrupted.

**What this module does not do.** It does not decide who should speak (the
Manager does), what anyone says (each agent's own brain does), or how a turn is
shown (surfaces do). It holds turn *state*, the same way Conversation holds a
transcript: records, never reasoning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Sequence

from src.conversation.conversation import Conversation, ConversationError, ParticipantKind


def utc_now() -> datetime:
    """The default clock, matching Conversation's. Injectable for tests."""
    return datetime.now(timezone.utc)


class FloorStatus(str, Enum):
    """Where a round stands."""

    IDLE = "idle"            # nobody has opened a round yet
    OPEN = "open"            # somebody holds the floor
    COMPLETE = "complete"    # every queued speaker has had their turn
    CANCELLED = "cancelled"  # the round was stopped before it finished


class TurnOutcome(str, Enum):
    """What happened when a participant's turn came, as a fact about it."""

    SPOKE = "spoke"              # said something
    DECLINED = "declined"        # answered, honestly, that it had nothing it could say
    FAILED = "failed"            # its turn broke
    SKIPPED = "skipped"          # it had left the room before its turn came
    CANCELLED = "cancelled"      # the round was stopped before its turn came
    INTERJECTED = "interjected"  # a person spoke out of turn


@dataclass(frozen=True)
class TurnRecord:
    """One turn, and what became of it. Frozen: an account of what happened."""

    speaker: str
    outcome: TurnOutcome
    when: datetime = field(default_factory=utc_now)
    note: str = ""
    round: int = 0

    def describe(self) -> str:
        return f"{self.speaker}: {self.outcome.value}" + (f" ({self.note})" if self.note else "")


class Floor:
    """The turn state of one conversation."""

    def __init__(self, conversation: Conversation, *, id: str | None = None) -> None:
        if not isinstance(conversation, Conversation):
            raise ConversationError("a floor belongs to a Conversation")
        self._conversation = conversation
        self.id = id or uuid.uuid4().hex[:12]
        self._status = FloorStatus.IDLE
        self._holder: str | None = None
        self._pending: list[str] = []
        self._history: list[TurnRecord] = []
        self._round = 0

    def rebind(self, conversation: Conversation) -> None:
        """Follow the room this floor belongs to when a saved session is
        resumed into an already-open room object."""
        if not isinstance(conversation, Conversation):
            raise ConversationError("a floor belongs to a Conversation")
        self._conversation = conversation

    # ------------------------------------------------------------------
    # Reading the floor
    # ------------------------------------------------------------------

    @property
    def status(self) -> FloorStatus:
        return self._status

    @property
    def holder(self) -> str | None:
        """Who may speak now, or None when nobody holds the floor."""
        return self._holder

    @property
    def pending(self) -> tuple[str, ...]:
        """Who is still due a turn in this round, in order, after the holder."""
        return tuple(self._pending)

    @property
    def history(self) -> tuple[TurnRecord, ...]:
        return tuple(self._history)

    @property
    def round(self) -> int:
        return self._round

    @property
    def is_open(self) -> bool:
        return self._status is FloorStatus.OPEN

    # ------------------------------------------------------------------
    # Rounds
    # ------------------------------------------------------------------

    def open(self, order: Sequence[str]) -> str:
        """Open a round with ``order`` as the speakers, and give the floor to
        the first. Every name must be in the room now; a repeated name is one
        turn, not two. Returns who holds the floor."""
        if self._status is FloorStatus.OPEN:
            raise ConversationError("a round is already open - finish or cancel it first")
        speakers: list[str] = []
        for name in order:
            participant = self._conversation.participant(name)
            if participant is None:
                raise ConversationError(
                    f"'{name}' is not in this conversation and cannot be given a turn"
                )
            if participant.name not in speakers:
                speakers.append(participant.name)
        if not speakers:
            raise ConversationError("a round needs at least one speaker")
        self._round += 1
        self._status = FloorStatus.OPEN
        self._holder = speakers[0]
        self._pending = speakers[1:]
        return self._holder

    def check(self, name: str) -> str:
        """Refuse anyone but the holder, and anyone who has left the room.
        Returns the holder's own spelling."""
        if self._status is not FloorStatus.OPEN or self._holder is None:
            raise ConversationError("nobody holds the floor - no round is open")
        if " ".join(str(name).split()).lower() != self._holder.lower():
            raise ConversationError(
                f"'{name}' does not hold the floor - it is {self._holder}'s turn"
            )
        if not self._conversation.has(self._holder):
            raise ConversationError(f"'{self._holder}' is no longer in this conversation")
        return self._holder

    def finish_turn(self, name: str, outcome: TurnOutcome, note: str = "") -> str | None:
        """Record the holder's turn and pass the floor on. Anyone due next who
        has left the room is skipped, on the record. Returns the new holder, or
        None when the round is complete."""
        holder = self.check(name)
        if outcome in (TurnOutcome.SKIPPED, TurnOutcome.CANCELLED, TurnOutcome.INTERJECTED):
            raise ConversationError(f"'{outcome.value}' is not how a held turn ends")
        self._history.append(TurnRecord(holder, outcome, note=note, round=self._round))
        return self._advance()

    def _advance(self) -> str | None:
        while self._pending:
            candidate = self._pending.pop(0)
            if self._conversation.has(candidate):
                self._holder = candidate
                return candidate
            self._history.append(
                TurnRecord(candidate, TurnOutcome.SKIPPED, note="left the room", round=self._round)
            )
        self._holder = None
        self._status = FloorStatus.COMPLETE
        return None

    def interject(self, name: str, note: str = "") -> None:
        """A person speaking out of turn. Recorded; nobody's place is lost.
        Only a person may interject - an agent speaks when it holds the floor."""
        participant = self._conversation.participant(name)
        if participant is None:
            raise ConversationError(f"'{name}' is not in this conversation")
        if participant.kind is not ParticipantKind.PERSON:
            raise ConversationError(
                f"'{participant.name}' is an agent - an agent speaks when it holds the floor"
            )
        interrupted = f"interrupted {self._holder}" if self._holder else ""
        self._history.append(
            TurnRecord(participant.name, TurnOutcome.INTERJECTED,
                       note=note or interrupted, round=self._round)
        )

    def cancel(self, note: str = "") -> None:
        """Stop the round. Whoever held or was due the floor is recorded as
        cancelled. Idempotent: cancelling a round that is not open changes
        nothing."""
        if self._status is not FloorStatus.OPEN:
            return
        for name in ([self._holder] if self._holder else []) + self._pending:
            self._history.append(TurnRecord(name, TurnOutcome.CANCELLED, note=note, round=self._round))
        self._holder = None
        self._pending = []
        self._status = FloorStatus.CANCELLED

    # ------------------------------------------------------------------
    # Saving and restoring
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self._status.value,
            "holder": self._holder,
            "pending": list(self._pending),
            "round": self._round,
            "history": [
                {"speaker": r.speaker, "outcome": r.outcome.value, "when": r.when.isoformat(),
                 "note": r.note, "round": r.round}
                for r in self._history
            ],
        }

    @classmethod
    def from_dict(cls, conversation: Conversation, data: dict) -> "Floor":
        floor = cls(conversation, id=str(data["id"]))
        floor._status = FloorStatus(data.get("status", FloorStatus.IDLE.value))
        floor._holder = data.get("holder") or None
        floor._pending = [str(n) for n in data.get("pending", ())]
        floor._round = int(data.get("round", 0))
        floor._history = [
            TurnRecord(str(r["speaker"]), TurnOutcome(r["outcome"]),
                       when=datetime.fromisoformat(r["when"]), note=str(r.get("note", "")),
                       round=int(r.get("round", 0)))
            for r in data.get("history", ())
        ]
        return floor

    def describe(self) -> str:
        holder = f", {self._holder} holds the floor" if self._holder else ""
        return f"floor {self.id}: round {self._round} {self._status.value}{holder}"


def order_of(conversation: Conversation, names: Iterable[str]) -> tuple[str, ...]:
    """``names`` as the room spells them, keeping only those present - a small
    convenience for whoever builds a round, deciding nothing about order."""
    present = []
    for name in names:
        participant = conversation.participant(name)
        if participant is not None and participant.name not in present:
            present.append(participant.name)
    return tuple(present)
