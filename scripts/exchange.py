"""One request and its answer, recorded in the room they happened in.

Extracted from scripts/control_center.py, verbatim in behaviour, so
scripts/serve_ui.py can drive the exact same interaction path a terminal
session already uses - not a second implementation of "what happens when
somebody talks to Alfred", the same functions, imported from one place
instead of copied into two.

The room is a record, not a router. It is told who spoke and what was said
*after* the fact; it does not decide who answers, and removing it would
change what is remembered about this session and nothing about the answer.
"""

from __future__ import annotations

import re
from dataclasses import replace

from pathlib import Path

from src.channels.channel import Channel, speakable
from src.control_center.views import ConversationTurn, TrailStep
from src.conversation.conversation import (
    Conversation,
    Participant,
    ParticipantKind,
    Utterance,
)
from src.core.contracts import Task
from src.manager.collaboration_entry import (
    answer_with_collaboration,
    collaboration_candidates,
    start_collaboration,
)
from src.services.audit.action_log import (
    CONSEQUENTIAL_CAPABILITIES,
    ActionLogEntry,
    record_action,
    utc_now as audit_utc_now,
)
from src.services.memory.audio_recording import build_recording_record
from src.services.memory.capture import CAPTURED_KIND, CAPTURED_SOURCE, capture_intent
from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import build_personal_context_record, extract_personal_context_candidate
from src.services.memory.session import RETAINED_KIND, RETAINED_SOURCE, retention_requests
from src.services.memory.user_facts import extract_declared_facts, structure_date_fact
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Re-exported so existing callers (and tests) that import
# start_collaboration / collaboration_candidates / answer_with_collaboration
# from this module keep working unchanged. The one implementation lives in
# src/manager/collaboration_entry.py - see that module's docstring for why:
# the real, live conversation path (src/control_center/trail.py's observe())
# needs to call it too, and src never imports scripts.
__all__ = [
    "exchange",
    "retain_what_was_asked_for",
    "admit_invited",
    "memory_for",
    "retain_for",
    "start_collaboration",
    "collaboration_candidates",
    "answer_with_collaboration",
    # Real Audio Recording/Capture checkpoint: re-exported so
    # scripts/serve_ui.py - guarded (tests/test_live_ui.py) against
    # importing src.services.memory directly at all - can still reach
    # this one builder function, the identical "one shared extraction,
    # reached through this module" shape capture_intent/build_recording
    # already have for record_this.
    "build_recording_record",
    "record_shared_file",
]


def record_shared_file(conversation: Conversation, *, description: str, addressed: str | None = None) -> bool:
    """Record, in the room, that the person shared a file - REPAIR-01/02.

    A shared file is part of what happened in the conversation, exactly as
    something said is: "this PDF" in the person's next line can only mean
    the file they just shared if the room remembers it was shared. Recorded
    as the person's own act (they did share it), carrying the artifact id
    in its text so it is resolvable later - never a reply, never a turn
    routed to anyone, and never a second way to hold a conversation
    (``exchange`` above stays the only door for that).

    Returns False, recording nothing, when the room has no person in it to
    attribute the share to.
    """
    person = next((p.name for p in conversation.participants if not p.kind.is_agent), None)
    if person is None or not str(description or "").strip():
        return False
    conversation.say(Utterance(speaker=person, text=description, addressed=addressed))
    return True


def memory_for(manager, agent_name: str):
    """The real Memory behind ``agent_name``, resolved the one way this
    project already does it in src/control_center/observer.py: the
    Manager's own registered identity (``own_skills``, what the
    registry holds under his name) exposes no ``.memory`` directly -
    only ``ManagerAgent`` itself does - while a specialist's own agent
    object exposes ``.memory`` directly. Shared here so
    scripts/control_center.py's ``avatar`` command and
    scripts/serve_ui.py's `POST /avatar` route apply the identical
    rule rather than each inventing its own.

    Returns None for an unrecognised name, or for an agent with no
    Memory - never fabricates one.
    """
    manager_name = manager.own_skills.name if manager.own_skills else None
    if agent_name == manager_name:
        return manager.memory
    resolved = manager.registry.get(agent_name)
    return getattr(resolved, "memory", None) if resolved is not None else None


def retain_for(manager, agent_name: str):
    """The real, governed write path for ``agent_name``'s own memory -
    ``ManagerAgent.retain`` when ``agent_name`` is the Manager himself,
    or that same agent's own ``retain`` method otherwise (every
    ``CognitiveAgent``-based specialist - see
    ``src/agents/cognitive_agent.py`` - already exposes one, the
    identical governed policy-check-then-remember shape
    ``ManagerAgent.retain`` has, reached through Stage 12 item 7's own
    "one shared extraction, two callers" pattern for ``record_this``).

    Generic on purpose, never Alfred-specific: Real Audio Recording/
    Capture's own save route is the first caller, but nothing here
    names an agent, so a future specialist gains a governed recording-
    save path with no change to this function - only to whether that
    specialist is actually offered the capability.

    Returns None for an unrecognised name, or for an agent with no
    governed write path at all - never fabricates one.
    """
    manager_name = manager.own_skills.name if manager.own_skills else None
    if agent_name == manager_name:
        return manager.retain
    resolved = manager.registry.get(agent_name)
    return getattr(resolved, "retain", None) if resolved is not None else None


def exchange(
    channel: Channel,
    conversation: Conversation,
    request: str,
    *,
    addressed: str | None = None,
    speak: bool = False,
    action_log_path: "str | Path | None" = None,
):
    """One request and its answer, recorded in the room they happened in.

    The room is a record, not a router. It is told who spoke and what was said
    *after* the fact; it does not decide who answers, and removing it would
    change what is remembered about this session and nothing about the answer.

    ``addressed_in`` is asked and recorded rather than acted on. Today nothing
    routes on it - the Manager still decides, through the router, as it always
    has. It is recorded now because a voice layer will need to know who was
    being spoken to, and a transcript that never captured it could not be
    asked afterwards.

    ``addressed``, when given, is an explicit choice of who to speak to -
    Stage 17's agent-selector, not a guess from the words themselves. It is
    resolved through ``channel.manager.registry``, the identical canonical
    lookup the Manager already uses to answer "who is this" - never a new
    agent, and never fuzzy: a name that is not registered, exactly, is not an
    address. A resolved agent not yet in the room is joined before the turn
    runs, the same way a delegated specialist already joins to speak for
    itself below - choosing to speak with someone is choosing to have them
    present, not routing behind their back.

    ``speak`` (Stage 17.3), when True, calls the channel's own
    ``deliver()`` after the answer comes back - the one real place
    speech happens (see ``VoiceChannel.deliver()``'s own docstring) and
    the one real place Stage 17.2's ``speaking()`` presence
    synchronisation is wired. False (the default) is exactly this
    function's behaviour before Stage 17.3 existed: an answer, recorded
    in the room, never spoken. Nothing here plays audio itself, chooses
    a voice, or asserts a presence state directly - all of that is
    ``deliver()``'s own job, called at most once, never duplicated.

    ``action_log_path`` (Foundation Repair #4, reconciliation audit,
    2026-09-05), when given, durably records this turn in
    ``src.services.audit.action_log`` if - and only if - it was handled by
    one of ``CONSEQUENTIAL_CAPABILITIES`` (see that module's own docstring
    for exactly which, and why ``turn.tool_used`` is not the right signal);
    an ordinary conversational turn, or a read-only lookup, writes nothing.
    None (the default) writes nothing at all regardless - the identical opt-in shape
    ``speak`` already has, and for the same reason every test in this
    project that calls ``exchange()`` without deliberately choosing a
    real, isolated path must keep working exactly as it did before this
    parameter existed, never touching a real file by surprise. Production
    callers (``scripts/control_center.py``, ``scripts/serve_ui.py``) pass
    the real, shared default path explicitly.

    The turn rides along as opaque ``detail``. The room never looks inside it.
    """
    person = next(
        (p.name for p in conversation.participants if not p.kind.is_agent), "you"
    )
    inferred = conversation.addressed_in(request)
    to_agent: str | None = None
    if addressed:
        resolved = channel.manager.registry.get(addressed)
        if resolved is not None:
            to_agent = resolved.name
            if not conversation.has(to_agent):
                conversation.join(Participant(to_agent, ParticipantKind.AGENT))
    else:
        # An address only reaches the orchestrator when it names an *agent*
        # who is in the room. `addressed_in` already matches nothing but
        # current participants, so "present" is settled there; this drops
        # the case where the person addressed themselves, which is not a
        # request for anybody to answer.
        speaking_to = conversation.participant(inferred) if inferred else None
        to_agent = inferred if speaking_to is not None and speaking_to.kind.is_agent else None
        if to_agent is None and inferred is None:
            # "Arthur, who are you?" names a registered agent who has not
            # joined the room yet. The words address him exactly as the
            # page's agent selector would - so he is joined and asked, never
            # answered for by someone else. The Manager addressed by name is
            # already in the room and needs nothing here.
            named = _opening_agent_name(channel, request)
            if named is not None:
                to_agent = named
                if not conversation.has(to_agent):
                    conversation.join(Participant(to_agent, ParticipantKind.AGENT))

    conversation.say(
        Utterance(speaker=person, text=request, addressed=to_agent or inferred)
    )

    # What is passed is context, not an instruction. The Manager decides what
    # an address means - see ManagerAgent.decide - because a room that could
    # pick an agent would be a second router.
    response = channel.ask(request, addressed=to_agent)
    turn = response.turn

    # Foundation Repair #3 (reconciliation audit, 2026-09-05): run before the
    # turn is spoken or recorded, not after, so a corrected turn is what gets
    # said and what the room remembers - never the stale failure text first
    # and a silently-different return value second. See
    # _acknowledge_successful_retention's own docstring for exactly what this
    # does and does not change.
    retained = retain_what_was_asked_for(
        channel, conversation, request,
        handled_by_capability=turn.capability, handled_successfully=turn.succeeded,
        addressed_to=to_agent if turn.handled_by == to_agent else None,
    )
    if retained and not turn.succeeded:
        turn = _acknowledge_successful_retention(turn, retained, only_asked_to_remember=_only_asked_to_remember(request))
        response = replace(response, turn=turn, text=turn.outcome, speech=speakable(turn))

    # Stage 17.3: the real production speech/presence synchronisation
    # point. `deliver()` is what actually calls a real, possibly-
    # blocking speak() and what actually marks the true canonical
    # speaker (turn.handled_by) SPEAKING for exactly that long (see
    # VoiceChannel.deliver() and src/services/identity/presence.py's
    # speaking()). Its own return value carries `spoken`; nothing here
    # needs it, because `turn` already carries everything every
    # existing caller of this function reads.
    if speak:
        channel.deliver(response)

    answer = (turn.outcome or turn.detail or "").strip()
    speaker = turn.handled_by
    # Only a real agent speaks. `handled_by` carries a sentinel when nothing
    # matched the request, and joining that put a participant called "nobody"
    # in the room - found by exporting a snapshot and reading the participant
    # list. Asked of the registry rather than compared against the sentinel:
    # the question is "is this an agent?", and the registry is what knows.
    if speaker and channel.manager.registry.get(speaker) is None:
        speaker = ""
    if answer and speaker:
        # **A specialist speaks for itself.**
        #
        # The answer is attributed to whoever actually produced it, and when
        # that is a specialist the specialist joins the room to say it. It used
        # to be dropped instead: this guarded on `conversation.has(...)`, and
        # because the room opens with only the person and the Manager, every
        # delegated answer silently vanished from the transcript - a record
        # showing a question and no reply.
        #
        # The two ways of "fixing" that were not equal. Attributing the answer
        # to the Manager would have been the Manager speaking for a specialist,
        # which is exactly what must not happen: the words came from that
        # agent's own brain, memory and tools, and putting somebody else's name
        # on them is a lie about who did the work. So the specialist joins.
        #
        # Joining is what delegation already means. Alfred decided this agent
        # should do the work; it is participating, and the room should say so.
        # No agent is named here - the name comes from the turn.
        if not conversation.has(speaker):
            conversation.join(Participant(speaker, ParticipantKind.AGENT))
        conversation.say(Utterance(speaker=speaker, text=answer, detail=turn))

    admit_invited(conversation, turn)

    if action_log_path is not None and turn.capability in CONSEQUENTIAL_CAPABILITIES:
        record_action(
            ActionLogEntry(
                timestamp=audit_utc_now().isoformat(),
                actor=turn.handled_by,
                capability=turn.capability,
                succeeded=turn.succeeded,
            ),
            path=action_log_path,
        )

    return turn


# "Arthur, ...", "hey Arthur: ...", "OK Research Agent, ..." - a name opening
# the utterance, followed by a comma or colon. Which names are agents is the
# registry's answer, never a list here.
_OPENING_ADDRESS = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok(?:ay)?|yo)\s*,?\s+)?(?P<name>[A-Za-z][\w'\-]*(?:\s+[A-Za-z][\w'\-]*){0,2}?)\s*[,:]\s*\S"
)


def _opening_agent_name(channel: Channel, request: str) -> "str | None":
    """The registered specialist an utterance opens by addressing, or None.
    The Manager himself is excluded - he answers every unaddressed request
    already."""
    match = _OPENING_ADDRESS.match(str(request or ""))
    if match is None:
        return None
    wanted = " ".join(match.group("name").split()).lower()
    manager_self = getattr(channel.manager, "own_skills", None)
    for agent in channel.manager.registry.all_agents():
        if agent is manager_self:
            continue
        if str(getattr(agent, "name", "")).lower() == wanted:
            return agent.name
    return None


# A request that continues past the note ("... and tell me what you think")
# asked for something more than remembering. Grammar only.
_FURTHER_REQUEST = re.compile(
    r"\b(?:and|then|also|but)\s+(?:tell|show|explain|find|check|send|call|what|how|why|can|could|would|please)\b",
    re.IGNORECASE,
)


def _only_asked_to_remember(request: str) -> bool:
    """Whether the whole utterance was a request to remember something, and
    nothing else - the case where "I couldn't reason about that" was never a
    real failure of anything the person asked for (Pre-Stage-9 Campaign 2)."""
    requested = retention_requests([request])
    if not requested:
        return False
    note = requested[0].content
    return "?" not in request and not _FURTHER_REQUEST.search(note)


def _acknowledge_successful_retention(
    turn: ConversationTurn, retained: "tuple[MemoryRecord, ...]", *, only_asked_to_remember: bool = False,
) -> ConversationTurn:
    """Correct a turn that failed at its own routed task, but that
    nonetheless genuinely retained what was asked to be remembered -
    the exact honesty gap the reconciliation audit named: with no Brain
    (or a Brain-dependent capability otherwise unable to answer), "remember
    that my favorite color is blue" reported FAILED to the person while the
    retention succeeded anyway - because retain_what_was_asked_for is a
    separate, always-governed path (see its own docstring) that runs
    regardless of how, or whether, the routed task itself was handled.

    Deliberately narrow and purely additive:

    - Only ever called when the routed task did not already succeed on its
      own terms - a turn that succeeded is never touched, because there is
      nothing dishonest to correct.
    - Never hides the original failure. The person is still told exactly
      what could not be done; this only adds the true, separate fact that
      something else, asked in the same breath, was done anyway.
    - Rewrites the "Assessed" trail step and adds a "Memory" step to match
      the corrected verdict, rather than returning a turn whose headline
      outcome contradicts a trail still reading "not accepted: failed"
      immediately above it.
    """
    # Shown as whoever actually kept it: a note given to a specialist is that
    # specialist's own capture (``captured_by``); everything else the Manager's.
    kept_by: dict[str, int] = {}
    for record in retained:
        writer = (record.context or {}).get("captured_by") or turn.manager or turn.handled_by
        kept_by[writer] = kept_by.get(writer, 0) + 1
    steps = tuple(
        replace(step, detail="accepted (see note below)") if step.label == "Assessed" else step
        for step in turn.steps
    ) + tuple(
        TrailStep(
            label="Memory",
            detail=f"{count} record(s) retained - what was asked to be remembered",
            actor=writer,
        )
        for writer, count in kept_by.items()
    )
    remembered = "; ".join(record.content for record in retained)
    if only_asked_to_remember:
        # Nothing else was asked, so nothing else failed: just say it back.
        outcome = f"Got it - I've remembered that: {remembered}"
    else:
        note = "I've remembered what you asked me to, though: " + remembered
        original = turn.outcome.strip()
        outcome = f"{original} {note}".strip() if original else note
    return replace(
        turn,
        succeeded=True,
        outcome=outcome,
        steps=steps,
        memory_written=turn.memory_written + len(retained),
    )


def retain_what_was_asked_for(
    channel: Channel, conversation: Conversation, request: str,
    *, handled_by_capability: "str | None" = None, handled_successfully: bool = False,
    addressed_to: "str | None" = None,
) -> "tuple[MemoryRecord, ...]":
    """Keep the things the person asked to be kept, if the policy allows.

    Returns exactly the candidates the policy actually admitted - not every
    candidate considered, and never a count standing in for the records
    themselves. Foundation Repair #3 (reconciliation audit, 2026-09-05)
    added this return value so ``exchange()`` can tell an honest "I've
    remembered that" from nothing having happened at all; every existing
    caller that ignored the previous ``None`` return keeps working
    unchanged.

    **Session context stays in the room; only this crosses into memory.** The
    room holds everything said, which is what a session is for. A conversation
    that wrote every utterance into a durable store would be building the
    history-instead-of-knowledge pile the memory policy exists to prevent, at
    the fastest rate available - so nothing is persisted unless somebody asked
    for it in as many words.

    The Manager's own store, through the Manager's own governed write path -
    unless the person explicitly addressed a specialist who answered
    (``addressed_to``). Then a plain note ("Nova, remember that the windmill
    is due Friday") is that specialist's, written through its own governed
    ``retain``: it said "I've remembered that", so it is the one that must be
    able to recall it. A fact about the person stays in the one canonical
    record the Manager writes and every specialist reads, whoever was asked.
    The policy is still the authority - a candidate it refuses is dropped.

    Extraction knows nothing about rooms and this function knows nothing about
    admission. Neither could do the other's job.

    **Stage 12 addition: a bare, first-person self-introduction is also
    offered, alongside an explicit "remember that X".** "My name is
    Kelly. I was born September 27, 2006" asks to be kept just as
    plainly as "remember that X" does - see
    ``user_facts.py``'s own docstring for why recognising this small,
    fixed set of self-introduction grammars is not the guessing
    ``session.py`` already refuses to do. Every candidate, from either
    source, still passes through the identical governed policy gate
    below - nothing here writes anything on its own authority.
    """
    requested = retention_requests([request], conversation_id=conversation.id)
    declared = extract_declared_facts(request)

    # Pre-Stage-9 Campaign 1: a "remember that <personal fact>" ("remember
    # that my goal is X", "note that I'm learning Y") is user knowledge, so it
    # is filed in the one canonical personal-context store rather than as a
    # second, unkeyed note that would never supersede anything. When the
    # personal_context capability already handled this very turn it has
    # written that record itself, so it is not written twice.
    candidates: list[MemoryRecord] = []
    notes_for_specialist: list[MemoryRecord] = []
    agent_name = getattr(getattr(channel.manager, "own_skills", None), "name", "") or "manager"
    specialist_retain = (retain_for(channel.manager, addressed_to)
                         if addressed_to and addressed_to != agent_name else None)
    for note in requested:
        taught = extract_personal_context_candidate(note.content)
        if taught is None:
            (notes_for_specialist if specialist_retain is not None else candidates).append(note)
        elif not (handled_by_capability == "personal_context" and handled_successfully):
            candidates.append(build_personal_context_record(taught, agent_name=agent_name))
    candidates.extend(declared)

    # 0.2 Final Surgical Pass: genuine understanding, not one more fixed
    # phrase, for the wording neither deterministic extractor above
    # recognises ("I might forget this", "save what I just said", "don't
    # let me lose this", and anything else nobody wrote down here).
    #
    # Also skipped when capture.capture_intent recognises the request:
    # that is "record this [for X]"'s own territory, a *separate*
    # deterministic mechanism (see capture.py's own docstring) reached
    # through the record_this capability, which writes through its own
    # governed call already - never through this function. Without this
    # check a request like "record this for later: water the plants"
    # would be captured twice: once correctly by record_this, and once
    # more here with a second, Brain-composed guess at the same content.
    if not requested and not declared and not capture_intent(request):
        candidates.extend(_brain_judged_retention(channel, conversation, request))

    # A specialist does not keep user context - that is the Manager's (see
    # SpecialistMemoryPolicy). What it keeps for an explicit request is its
    # own explicit capture, the identical record "Arthur, record this for
    # later" makes (capture.build_recording): full confidence, because a
    # person asking is not a hunch, and who was asked as provenance.
    notes_for_specialist = [
        MemoryRecord(
            content=note.content, kind=CAPTURED_KIND, tags={"recording", "remember"},
            context={**(note.context or {}), "purpose": "remember", "confidence": 1.0,
                     "source": CAPTURED_SOURCE, "captured_by": addressed_to},
        )
        for note in notes_for_specialist
    ]
    retained: list[MemoryRecord] = []
    for candidate, write in ([(c, channel.manager.retain) for c in candidates]
                             + [(n, specialist_retain) for n in notes_for_specialist]):
        dated = _dated_if_recognisable(candidate)
        verdict = write(dated)
        if not verdict.allowed:
            logger.info("not retained: %s", verdict.reason)
        else:
            retained.append(dated)
    return tuple(retained)


def _brain_judged_retention(
    channel: Channel, conversation: Conversation, request: str
) -> tuple[MemoryRecord, ...]:
    """Ask the real Brain whether this remark, read together with the
    conversation around it, is genuinely asking for something to be kept -
    see ``ManagerBrain.discern_retention``'s own docstring for the exact
    question this answers and why it is a separate one from deliberation
    or open conversation.

    Honest by construction, never a special case here: a Manager with no
    usable Brain (``NullBrain``, or a real one that cannot currently
    reach its provider) answers ``discern_retention`` with its own
    unchanged "no view" - the identical degradation every other Brain
    consultation in this project already has - so this simply returns
    nothing and the turn behaves exactly as it did before this function
    existed.
    """
    brain = getattr(channel.manager, "brain", None)
    discern = getattr(brain, "discern_retention", None)
    if discern is None:
        return ()

    notes = tuple(line for line in conversation.transcript() if str(line).strip())
    decision = discern(Task(description=request), notes)
    if not decision.is_decided or not decision.reason.strip():
        return ()

    return (
        MemoryRecord(
            content=decision.reason,
            kind=RETAINED_KIND,
            tags={"recording"},
            context={
                "source": RETAINED_SOURCE,
                "conversation": conversation.id,
                "fact_type": "recording",
            },
        ),
    )


def _dated_if_recognisable(candidate) -> "MemoryRecord":
    """Enrich a candidate with a structured date when its own content
    names one a person recognisably stated - "remember that I was born
    September 27, 2006" becomes just as computable as the bare
    declarative form, without extraction and enrichment having to be
    the same pass. A candidate that already carries a date (from
    ``extract_declared_facts`` directly) is returned unchanged.
    """
    if candidate.context and "date" in candidate.context:
        return candidate
    structured = structure_date_fact(candidate.content)
    if structured is None:
        return candidate
    fact_type, parsed_date = structured
    context = dict(candidate.context or {})
    context.setdefault("fact_type", fact_type)
    context["date"] = parsed_date.isoformat()
    return replace(candidate, context=context, tags=candidate.tags | {fact_type})


def admit_invited(conversation: Conversation, turn) -> None:
    """Bring in whoever the Manager resolved an invitation to, if anyone.

    **A name crosses this boundary, never an agent.** The Manager decided who
    the person meant - it holds the registry and that is orchestration - and
    reported the answer as a name in the result payload. This reads the name
    and opens the door. Nothing here discovers, constructs or reaches a
    specialist, and there is no agent object in scope that it could put into a
    Participant even by mistake.

    Nothing is invented. When the Manager found no such specialist the payload
    carries None, the room is unchanged, and the answer already says plainly
    who is registered - which is more useful than silently agreeing that
    somebody exists because they were named.

    No agent is named here, so a specialist added later is invitable the day it
    registers with nothing in this file changing.
    """
    invited = turn.invited
    if invited and not conversation.has(invited):
        conversation.join(Participant(invited, ParticipantKind.AGENT))
