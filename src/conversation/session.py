"""One interaction session: the room, what was discussed in it, and what was
handed to whom - held together so it can outlive the process that held it.

Three records this package already has are enough to describe a session, so
this module composes them rather than inventing a fourth kind of state:

    Conversation   who is in the room and everything said in it
    Collaboration  the shared context one discussion accumulates, each piece
                   attributed to whoever contributed it (the Shared Context
                   Graph) - one per discussion
    Floor          whose turn it is in that discussion, and what became of
                   every turn

plus one small record of its own, ``DelegationRecord``: that the orchestrator
handed a piece of work to another participant, and what came back.

**A session is not a memory.** Nothing here decides what any agent retains, and
nothing here is read by an agent's brain on its own. Memory is what an agent's
own policy admits; a session is the record of an interaction. They answer
different questions - "what does this agent know?" and "what happened in this
room?" - and keeping them apart is what lets a session be saved, resumed and
shown on any surface without any agent's private memory travelling with it.

**Surface-independent by construction.** Nothing here knows about a terminal, a
browser, a phone, a headset or any other door. A session is identified by the
conversation's own id; whichever surface a person uses next can ask for it by
that id and continue it.

**No agent is named here,** and no participant count, order or role is fixed.
Who takes part is whoever was put in the room; who speaks next is whatever
order the orchestrator handed the floor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable

from src.conversation.collaboration import Collaboration, Contribution
from src.conversation.conversation import (
    Conversation,
    ConversationError,
    Participant,
    ParticipantKind,
    Utterance,
)
from src.conversation.floor import Floor

SESSION_FORMAT = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiscussionStatus(str, Enum):
    OPEN = "open"
    CONCLUDED = "concluded"
    CANCELLED = "cancelled"


@dataclass
class Discussion:
    """One discussion held in a session: its subject, its shared context and
    its turn state. ``id`` is its collaboration's id."""

    subject: str
    opened_by: str
    collaboration: Collaboration
    floor: Floor
    status: DiscussionStatus = DiscussionStatus.OPEN
    conclusion: str = ""

    @property
    def id(self) -> str:
        return self.collaboration.id

    @property
    def is_open(self) -> bool:
        return self.status is DiscussionStatus.OPEN

    def describe(self) -> str:
        return (
            f"discussion {self.id} ({self.status.value}) on '{self.subject}': "
            f"{len(self.collaboration.participants)} participant(s), "
            f"{len(self.collaboration.contributions)} contribution(s), round {self.floor.round}"
        )


@dataclass(frozen=True)
class DelegationRecord:
    """That ``originator`` handed ``request`` to ``target``, and what came
    back. Both names are runtime identities - who decided and who did the
    work - never words taken from the request."""

    originator: str
    target: str
    request: str
    succeeded: bool
    outcome: str = ""
    reason: str = ""
    when: datetime = field(default_factory=utc_now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def describe(self) -> str:
        state = "completed" if self.succeeded else "not completed"
        return f"{self.originator} -> {self.target}: {state}"


class Session:
    """A conversation, the discussions held in it, and its delegations."""

    def __init__(self, conversation: Conversation) -> None:
        if not isinstance(conversation, Conversation):
            raise ConversationError("a session is built on a Conversation")
        self.conversation = conversation
        self._discussions: list[Discussion] = []
        self._delegations: list[DelegationRecord] = []

    @property
    def id(self) -> str:
        return self.conversation.id

    # ------------------------------------------------------------------
    # Discussions
    # ------------------------------------------------------------------

    @property
    def discussions(self) -> tuple[Discussion, ...]:
        return tuple(self._discussions)

    @property
    def active_discussion(self) -> Discussion | None:
        """The discussion still open in this session, if any."""
        for discussion in reversed(self._discussions):
            if discussion.is_open:
                return discussion
        return None

    def open_discussion(self, subject: str, *, opened_by: str, members: Iterable[str]) -> Discussion:
        """Start a discussion on ``subject``. ``members`` are everybody whose
        words may enter its shared context - each must be in the room; the
        opener is always one. Only one discussion is open at a time: a room
        holding two open discussions could not say which one a remark belongs
        to."""
        if self.active_discussion is not None:
            raise ConversationError("a discussion is already open in this session - conclude it first")
        if not str(subject).strip():
            raise ConversationError("a discussion must have a subject")
        names = [opened_by, *members]
        participants: list[Participant] = []
        for name in names:
            participant = self.conversation.participant(name)
            if participant is None:
                raise ConversationError(f"'{name}' is not in this conversation")
            if participant not in participants:
                participants.append(participant)
        collaboration = Collaboration(task=subject, participants=participants)
        discussion = Discussion(
            subject=collaboration.task,
            opened_by=self.conversation.participant(opened_by).name,
            collaboration=collaboration,
            floor=Floor(self.conversation, id=collaboration.id),
        )
        self._discussions.append(discussion)
        return discussion

    def admit(self, discussion: Discussion, name: str) -> None:
        """Let somebody who joined the room after a discussion opened take
        part in it. They must be in the room first."""
        participant = self.conversation.participant(name)
        if participant is None:
            raise ConversationError(f"'{name}' is not in this conversation")
        discussion.collaboration.join(participant)

    def conclude(self, discussion: Discussion, conclusion: str = "") -> None:
        discussion.floor.cancel("the discussion concluded")
        discussion.status = DiscussionStatus.CONCLUDED
        discussion.conclusion = str(conclusion or "").strip()
        discussion.collaboration.close()

    def cancel(self, discussion: Discussion, note: str = "") -> None:
        discussion.floor.cancel(note or "the discussion was cancelled")
        discussion.status = DiscussionStatus.CANCELLED
        discussion.collaboration.close()

    # ------------------------------------------------------------------
    # Delegations
    # ------------------------------------------------------------------

    @property
    def delegations(self) -> tuple[DelegationRecord, ...]:
        return tuple(self._delegations)

    def record_delegation(self, record: DelegationRecord) -> DelegationRecord:
        if not isinstance(record, DelegationRecord):
            raise ConversationError("only a DelegationRecord may be recorded")
        self._delegations.append(record)
        return record

    # ------------------------------------------------------------------
    # Saving and restoring
    # ------------------------------------------------------------------

    def to_dict(self, *, detail_of: Callable[[Any], Any] | None = None) -> dict:
        """Everything needed to continue this session, as plain data.

        ``Utterance.detail`` is opaque to this package, so it is kept only as
        whatever ``detail_of`` turns it into - by default, only when it is
        already plain data. What was said, by whom and to whom is always kept.
        """
        conversation = self.conversation
        return {
            "format": SESSION_FORMAT,
            "id": conversation.id,
            "started_at": conversation.started_at.isoformat(),
            "participants": [{"name": p.name, "kind": p.kind.value} for p in conversation.participants],
            "utterances": [
                {
                    "speaker": u.speaker,
                    "text": _kept_text(u),
                    "when": u.when.isoformat(),
                    "addressed": u.addressed,
                    "detail": (detail_of or _plain)(u.detail),
                }
                for u in conversation.utterances
            ],
            "discussions": [
                {
                    "subject": d.subject,
                    "opened_by": d.opened_by,
                    "status": d.status.value,
                    "conclusion": d.conclusion,
                    "collaboration": {
                        "id": d.collaboration.id,
                        "task": d.collaboration.task,
                        "started_at": d.collaboration.started_at.isoformat(),
                        "closed_at": d.collaboration.closed_at.isoformat() if d.collaboration.closed_at else None,
                        "participants": [
                            {"name": p.name, "kind": p.kind.value} for p in d.collaboration.participants
                        ],
                        "contributions": [
                            {"id": c.id, "source": c.source, "content": c.content, "kind": c.kind,
                             "relates_to": list(c.relates_to), "created_at": c.created_at.isoformat()}
                            for c in d.collaboration.contributions
                        ],
                    },
                    "floor": d.floor.to_dict(),
                }
                for d in self._discussions
            ],
            "delegations": [
                {"id": r.id, "originator": r.originator, "target": r.target, "request": r.request,
                 "succeeded": r.succeeded, "outcome": r.outcome, "reason": r.reason,
                 "when": r.when.isoformat()}
                for r in self._delegations
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        if int(data.get("format", 0)) != SESSION_FORMAT:
            raise ConversationError(f"unknown session format {data.get('format')!r}")
        conversation = Conversation.restore(
            (_participant(p) for p in data.get("participants", ())),
            (
                Utterance(
                    speaker=u["speaker"], text=u["text"], when=datetime.fromisoformat(u["when"]),
                    addressed=u.get("addressed"), detail=u.get("detail"),
                )
                for u in data.get("utterances", ())
            ),
            id=str(data["id"]),
            started_at=datetime.fromisoformat(data["started_at"]),
        )
        session = cls(conversation)
        for d in data.get("discussions", ()):
            c = d["collaboration"]
            collaboration = Collaboration.restore(
                c["task"],
                (_participant(p) for p in c.get("participants", ())),
                (
                    Contribution(
                        source=x["source"], content=x["content"], kind=x.get("kind", ""),
                        relates_to=tuple(x.get("relates_to", ())),
                        created_at=datetime.fromisoformat(x["created_at"]), id=x["id"],
                    )
                    for x in c.get("contributions", ())
                ),
                id=str(c["id"]),
                started_at=datetime.fromisoformat(c["started_at"]),
                closed_at=datetime.fromisoformat(c["closed_at"]) if c.get("closed_at") else None,
            )
            session._discussions.append(
                Discussion(
                    subject=d["subject"],
                    opened_by=d["opened_by"],
                    collaboration=collaboration,
                    floor=Floor.from_dict(conversation, d["floor"]),
                    status=DiscussionStatus(d.get("status", DiscussionStatus.OPEN.value)),
                    conclusion=d.get("conclusion", ""),
                )
            )
        for r in data.get("delegations", ()):
            session._delegations.append(
                DelegationRecord(
                    originator=r["originator"], target=r["target"], request=r["request"],
                    succeeded=bool(r["succeeded"]), outcome=r.get("outcome", ""),
                    reason=r.get("reason", ""), when=datetime.fromisoformat(r["when"]), id=r["id"],
                )
            )
        return session

    def describe(self) -> str:
        return (
            f"session {self.id}: {len(self.conversation.participants)} participant(s), "
            f"{len(self.conversation)} utterance(s), {len(self._discussions)} discussion(s), "
            f"{len(self._delegations)} delegation(s)"
        )


def _participant(data: dict) -> Participant:
    return Participant(str(data["name"]), ParticipantKind(data.get("kind", ParticipantKind.AGENT.value)))


def _kept_text(utterance: Any) -> str:
    """What of an utterance a saved session keeps (GM-13): its words - unless
    the person marked it, or the owner's rules made it, sensitive or above;
    then only that something was said. The live room still holds the words
    for as long as it is open."""
    detail = utterance.detail if isinstance(utterance.detail, dict) else {}
    level = str(detail.get("sensitivity") or "")
    if level:
        from src.core.sensitivity import rank

        if rank(level) >= rank("sensitive"):
            return f"[{level} - not kept in the saved session]"
    return utterance.text


def _plain(detail: Any) -> Any:
    """Keep ``detail`` only when it is already plain data."""
    if detail is None or isinstance(detail, (str, int, float, bool)):
        return detail
    if isinstance(detail, (list, tuple)):
        kept = [_plain(x) for x in detail]
        return kept if all(x is not None for x in kept) else None
    if isinstance(detail, dict) and all(isinstance(k, str) for k in detail):
        kept = {k: _plain(v) for k, v in detail.items()}
        return kept if all(v is not None or detail[k] is None for k, v in kept.items()) else None
    return None
