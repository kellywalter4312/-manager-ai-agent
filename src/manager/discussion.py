"""Phase 10 - a discussion among the room's participants, chaired by the
orchestrator and held through each participant's own runtime.

    person asks for a discussion
      -> the orchestrator's own capability resolves subject and participants
      -> a Discussion opens in the Session (shared context + floor)
      -> each participant, in turn, is given the discussion so far as
         attributed shared context and answers through its OWN brain
      -> every answer is said in the room under its speaker's own name and
         published to the shared context, naming what it responded to
      -> the next speaker sees it
      -> the orchestrator concludes when asked, from what was actually said

**Not strings put side by side.** Each speaker answers after the ones before it
and is handed what they said, as attributed shared context (src/core/
shared_context.py) - so a participant's turn can respond to another's. Each
contribution records which earlier contributions its speaker was shown
(``relates_to``), so the thread of who responded to whom is kept, not guessed.

**Each participant speaks for itself.** A specialist answers through its own
conversation path (``general_conversation``: its own brain, its own honesty
when it cannot reason); the orchestrator answers through its own brain. A
discussion turn never goes through ``execute()``: talking about building
something must not build it, so no capability runs because it was discussed.
The words recorded under a name are the words that agent's own runtime
produced, and the name is the runtime's, never one taken from the text.

**Honest with or without a language model.** A participant whose brain cannot
reason says so, and that is recorded as a declined turn - a discussion is
never padded with invented views. The conclusion is the orchestrator's own
synthesis when its brain can make one; when it cannot, it is a plain account
of who contributed how much, and says that no synthesis was made.

**No participant is named here, and none is ordered here.** Who takes part is
whoever the person named (resolved by the registry) or whoever is in the room;
each round hears the participants in the order they joined the discussion, and
the chair responds last - a role, never a name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.conversation.collaboration import Contribution
from src.conversation.conversation import ConversationError, Participant, ParticipantKind, Utterance
from src.conversation.floor import TurnOutcome
from src.conversation.session import Discussion, Session
from src.core.contracts import Task
from src.core.shared_context import SharedNote, shared_notes, with_shared_context
from src.manager.manager import ManagerAgent
from src.utils.logging import get_logger

logger = get_logger(__name__)

DISCUSSION_KIND = "discussion"
PERSON_KIND = "person"
CONCLUSION_KIND = "conclusion"


class DiscussionError(Exception):
    """A discussion was asked for something it cannot do - said plainly."""


@dataclass(frozen=True)
class DiscussionTurn:
    """One turn as it happened: who, what they said (or why they did not),
    and what became of the turn."""

    speaker: str
    text: str
    outcome: TurnOutcome
    contribution: str | None = None

    def line(self) -> str:
        if self.outcome is TurnOutcome.SPOKE:
            return f"{self.speaker}: {self.text}"
        return f"{self.speaker} ({self.outcome.value}): {self.text}"

    def to_dict(self) -> dict:
        return {"speaker": self.speaker, "text": self.text, "outcome": self.outcome.value,
                "contribution": self.contribution}


def chair_of(manager: ManagerAgent) -> str:
    return manager.own_skills.name if manager.own_skills is not None else "manager"


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def open_discussion(
    manager: ManagerAgent, session: Session, subject: str, *, speakers: Sequence[str] | None = None
) -> tuple[Discussion, tuple[str, ...]]:
    """Open a discussion on ``subject``.

    ``speakers`` are the names the person asked for; each is resolved through
    the registry and brought into the room if it is not there yet (joining is
    what taking part means - the same rule a delegated specialist already
    follows). A name nobody is registered under is not invented; it is
    returned as unknown. With no speakers named, the discussion is with every
    agent already in the room. Everyone who is a person in the room is a member
    too, so what they say during the discussion becomes part of it.

    Returns the discussion and the names that could not be resolved."""
    room = session.conversation
    chair = chair_of(manager)
    unknown: list[str] = []
    agents: list[str] = []
    if speakers:
        for name in speakers:
            agent = manager.registry.get(name)
            if agent is None or agent.name == chair:
                if agent is None:
                    unknown.append(str(name))
                continue
            if not room.has(agent.name):
                room.join(Participant(agent.name, ParticipantKind.AGENT))
            if agent.name not in agents:
                agents.append(agent.name)
    else:
        agents = [
            p.name for p in room.agents
            if p.name != chair and manager.registry.get(p.name) is not None
        ]
    if not agents:
        raise DiscussionError(
            "there is nobody else here to discuss it with - invite someone first, "
            "or name who should take part"
        )
    people = [p.name for p in room.participants if not p.kind.is_agent]
    try:
        discussion = session.open_discussion(subject, opened_by=chair, members=[*agents, *people])
    except ConversationError as exc:
        raise DiscussionError(str(exc)) from exc
    return discussion, tuple(unknown)


def round_order(manager: ManagerAgent, session: Session, discussion: Discussion) -> tuple[str, ...]:
    """Who speaks in the next round: every agent taking part and still in the
    room, in the order they joined the discussion, then the chair - who
    responds to what was said. People are never queued; they speak when they
    choose."""
    chair = chair_of(manager)
    room = session.conversation
    # Still in the room AND still a registered agent: an agent removed from
    # the ecosystem mid-discussion has no runtime to speak with, and is not
    # given the floor as though it did.
    members = [
        p.name for p in discussion.collaboration.participants
        if p.kind.is_agent and p.name != chair and room.has(p.name)
        and manager.registry.get(p.name) is not None
    ]
    if room.has(chair):
        members.append(chair)
    return tuple(members)


# ---------------------------------------------------------------------------
# Rounds and turns
# ---------------------------------------------------------------------------


def run_round(manager: ManagerAgent, session: Session, discussion: Discussion) -> tuple[DiscussionTurn, ...]:
    """Give every participant one turn, in order, on the floor."""
    if not discussion.is_open:
        raise DiscussionError(f"the discussion on '{discussion.subject}' is {discussion.status.value}")
    order = round_order(manager, session, discussion)
    if not any(name != chair_of(manager) for name in order):
        raise DiscussionError("nobody taking part in this discussion is still in the room")
    discussion.floor.open(order)
    turns: list[DiscussionTurn] = []
    while discussion.floor.holder is not None:
        turns.append(take_turn(manager, session, discussion, discussion.floor.holder))
    return tuple(turns)


def discussion_notes(discussion: Discussion, for_participant: str) -> tuple[SharedNote, ...]:
    """What ``for_participant`` is shown of the discussion so far: every
    contribution, attributed - read through the collaboration's own
    membership check, so somebody not taking part is shown nothing."""
    scope = f"the discussion on '{discussion.subject}'"
    return tuple(
        SharedNote(source=c.source, content=c.content, kind=c.kind, scope=scope)
        for c in discussion.collaboration.context_for(for_participant, limit=200)
    )


def take_turn(manager: ManagerAgent, session: Session, discussion: Discussion, speaker: str) -> DiscussionTurn:
    """``speaker``'s turn: only the holder of the floor may take it."""
    holder = discussion.floor.check(speaker)
    room = session.conversation
    visible = discussion.collaboration.context_for(holder, limit=200)
    task = with_shared_context(
        Task(description=turn_prompt(discussion.subject, has_context=bool(visible))),
        discussion_notes(discussion, holder),
    )
    try:
        spoke, text = _speak(manager, holder, task)
    except Exception as exc:  # noqa: BLE001 - one participant must not end the discussion
        logger.exception("discussion turn for '%s' failed", holder)
        note = f"{type(exc).__name__}: {exc}"
        discussion.floor.finish_turn(holder, TurnOutcome.FAILED, note=note)
        return DiscussionTurn(holder, note, TurnOutcome.FAILED)

    detail = {"discussion": discussion.id, "round": discussion.floor.round}
    if not spoke:
        room.say(Utterance(speaker=holder, text=text, detail={**detail, "declined": True}))
        discussion.floor.finish_turn(holder, TurnOutcome.DECLINED, note=text)
        return DiscussionTurn(holder, text, TurnOutcome.DECLINED)

    room.say(Utterance(speaker=holder, text=text, detail=detail))
    contribution = discussion.collaboration.contribute(
        Contribution(source=holder, content=text, kind=DISCUSSION_KIND,
                     relates_to=tuple(c.id for c in visible))
    )
    discussion.floor.finish_turn(holder, TurnOutcome.SPOKE)
    return DiscussionTurn(holder, text, TurnOutcome.SPOKE, contribution=contribution.id)


def turn_prompt(subject: str, *, has_context: bool) -> str:
    """What a participant is asked on its turn. The same question for every
    participant - who answers it is what makes the answers differ."""
    if has_context:
        return (
            f"We are discussing: {subject}. Give your own view, responding to what "
            "the others have already said."
        )
    return f"We are discussing: {subject}. Give your own view."


def _speak(manager: ManagerAgent, name: str, task: Task) -> tuple[bool, str]:
    """(spoke, words) from ``name``'s own runtime - never ``execute()``."""
    agent = manager.registry.get(name)
    if agent is None:
        return False, f"{name} is not a registered agent, so has nothing to say here."
    if agent is manager.own_skills:
        decision = manager.brain.converse(task, shared_notes(task))
        if decision.is_decided and decision.reason.strip():
            return True, decision.reason.strip()
        return False, (
            "I have no view to add - my reasoning isn't available right now "
            f"({decision.reason or 'no language model configured'})."
        )
    converse = getattr(agent, "general_conversation", None)
    if not callable(converse):
        return False, f"{agent.name} has no way to take part in a discussion."
    result = converse(task)
    text = (result.output or result.error or "").strip()
    if result.succeeded and text:
        return True, text
    return False, text or f"{agent.name} had nothing it could say."


# ---------------------------------------------------------------------------
# People, conclusion, cancellation
# ---------------------------------------------------------------------------


def note_person_remark(session: Session, discussion: Discussion, person: str, text: str) -> str | None:
    """A person spoke while the discussion was open: on the floor it is an
    interjection, and what they said becomes part of the shared context the
    next speakers are shown. Returns the contribution id, or None when the
    speaker is not a person taking part."""
    participant = session.conversation.participant(person)
    if participant is None or participant.kind.is_agent or not discussion.collaboration.has(participant.name):
        return None
    discussion.floor.interject(participant.name)
    contribution = discussion.collaboration.contribute(
        Contribution(source=participant.name, content=text, kind=PERSON_KIND)
    )
    return contribution.id


def note_agent_reply(session: Session, discussion: Discussion, speaker: str, text: str) -> str | None:
    """An agent taking part answered the person directly while the discussion
    was open (they addressed it): its answer joins the shared context too."""
    if not discussion.collaboration.has(speaker) or not session.conversation.has(speaker):
        return None
    visible = discussion.collaboration.context_for(speaker, limit=200)
    contribution = discussion.collaboration.contribute(
        Contribution(source=speaker, content=text, kind=DISCUSSION_KIND,
                     relates_to=tuple(c.id for c in visible))
    )
    return contribution.id


def conclude(manager: ManagerAgent, session: Session, discussion: Discussion) -> DiscussionTurn:
    """Close the discussion with the chair's conclusion.

    The chair's own synthesis, from what was actually contributed, when its
    brain can make one. Otherwise a plain account of who contributed how much
    - counts and names from the record, and a statement that no synthesis was
    made, never an invented summary."""
    chair = chair_of(manager)
    contributions = discussion.collaboration.contributions
    notes = tuple(
        SharedNote(source=c.source, content=c.content, kind=c.kind,
                   scope=f"the discussion on '{discussion.subject}'").line()
        for c in contributions
    )
    task = Task(description=(
        f"Conclude the discussion on: {discussion.subject}. Say what the participants "
        "agreed, where they differed, and what remains open."
    ))
    decision = manager.brain.converse(task, notes) if contributions else None
    if decision is not None and decision.is_decided and decision.reason.strip():
        text, synthesised = decision.reason.strip(), True
    else:
        by: dict[str, int] = {}
        for c in contributions:
            by[c.source] = by.get(c.source, 0) + 1
        who = ", ".join(f"{name} ({count})" for name, count in by.items()) or "nobody"
        text = (
            f"The discussion on '{discussion.subject}' is concluded after "
            f"{discussion.floor.round} round(s). Contributions: {who}. "
            "No synthesis was made - my reasoning isn't available right now, so the "
            "contributions above stand as they were said."
        )
        synthesised = False
    detail = {"discussion": discussion.id, "conclusion": True, "synthesised": synthesised}
    if session.conversation.has(chair):
        session.conversation.say(Utterance(speaker=chair, text=text, detail=detail))
    contribution_id = None
    if discussion.collaboration.has(chair):
        contribution_id = discussion.collaboration.contribute(
            Contribution(source=chair, content=text, kind=CONCLUSION_KIND,
                         relates_to=tuple(c.id for c in contributions))
        ).id
    session.conclude(discussion, text)
    return DiscussionTurn(chair, text, TurnOutcome.SPOKE, contribution=contribution_id)


def act_on(manager: ManagerAgent, session: Session, request: dict) -> dict:
    """Carry out a discussion request the orchestrator's capability resolved.

    Returns what happened as plain data a surface can show: the action, the
    discussion's id, subject and status, each turn, and a note when the
    request could not be carried out (said, never swallowed)."""
    action = str(request.get("action") or "")
    active = session.active_discussion
    try:
        if action == "open":
            if active is not None:
                return _report(action, active, (), note=(
                    f"A discussion on '{active.subject}' is already open - conclude or cancel it first."
                ))
            discussion, unknown = open_discussion(
                manager, session, str(request.get("subject") or ""),
                speakers=request.get("with") or None,
            )
            turns = run_round(manager, session, discussion)
            note = f"No agent is registered as: {', '.join(unknown)}." if unknown else ""
            return _report(action, discussion, turns, note=note)
        if active is None:
            return {"action": action, "note": "No discussion is open in this conversation.", "turns": []}
        if action == "continue":
            return _report(action, active, run_round(manager, session, active))
        if action == "conclude":
            return _report(action, active, (conclude(manager, session, active),))
        if action == "cancel":
            session.cancel(active)
            return _report(action, active, (), note=f"The discussion on '{active.subject}' was cancelled.")
    except (DiscussionError, ConversationError) as exc:
        # Said, never raised into the turn: a discussion that cannot go on
        # (somebody no longer in the room, a round already open) is reported.
        return {"action": action, "note": str(exc)[:1].upper() + str(exc)[1:].rstrip(".") + ".", "turns": []}
    return {"action": action, "note": f"'{action}' is not something a discussion can do.", "turns": []}


def _report(action: str, discussion: Discussion, turns: Sequence[DiscussionTurn], *, note: str = "") -> dict:
    return {
        "action": action,
        "id": discussion.id,
        "subject": discussion.subject,
        "status": discussion.status.value,
        "round": discussion.floor.round,
        "turns": [t.to_dict() for t in turns],
        "note": note,
    }
