"""Clarification, guided-activity, and consequential-action-confirmation
handling for open_conversation.

Three related, general behaviours, kept in one place because all three
are reached from the identical spot - the top of ``open_conversation``,
before an ordinary conversational reply is composed - and all three
share the same shape: something is genuinely in progress across turns,
tracked in ``ActivitySessions`` (never Memory, never a second Brain -
see that module's own docstring), and this file decides what that means
for the current remark.

**Consequential-action confirmation (Phase 4).** When a governed
capability is about to perform a real, external, side-effecting action
- sending a message, placing a call, to any party at all, never a
name-specific feature - it describes the action and raises a
``PendingConfirmation`` instead of executing. The very next remark is
checked, deterministically (``_try_resolve_confirmation``, a fixed
yes/no vocabulary, never a Brain call - this must resolve identically
under Full Brain and Half Brain), against exactly that pending action;
a clear yes executes it for real, a clear no cancels it, anything else
leaves it pending. This is what keeps "the Brain proposed it" and "it
actually happened" two separate, governed steps for every communication
provider this project has or will ever have.

**Clarification.** When the Brain notices a remark states two or more
specific, genuinely unresolved candidate values for the same thing ("it's
either Tuesday or Wednesday"), a clarifying question is raised and
tracked. The very next remark is checked, deterministically, against the
candidates actually offered; a match resolves it - the chosen value
becomes a real, governed memory fact, and any existing record naming one
of the *other* candidates is removed, so both are never simultaneously
treated as current truth. This is a general mechanism keyed on whatever
candidates the Brain names, never a hardcoded fact type.

**Guided activity.** When the Brain notices a remark genuinely asks to
begin an interactive learning/practice/quiz session, one starts: a
question is composed for the stated subject, and every subsequent remark
while a session is active is graded as an answer to it (or recognised as
a request to stop) rather than answered as ordinary conversation. Scoring
is this module's own deterministic bookkeeping - the Brain judges
correctness once per answer; the running count is exact, incremented
here, never re-derived by asking the Brain to remember earlier turns
itself. A short progress note is retained to real, governed memory when a
session ends with at least one answer given.

Both checks are additive and reversible: neither commits anything before
the person is asked, and both simply return ``None`` - "nothing to do
here" - the moment either ``request.activity_sessions`` or
``request.activity_brain`` is absent (a bare construction, an older
caller, or a Manager with no Brain configured), so ``open_conversation``
falls through to its own unchanged, ordinary behaviour exactly as it
always has.
"""

from __future__ import annotations

from typing import Sequence

from src.core.contracts import TaskResult
from src.manager.activity_session import (
    ActivityBrainOps,
    ActivitySessions,
    GuidedActivityState,
    PendingClarification,
    PendingConfirmation,
)
from src.manager.memory_policy import MemoryCategory
from src.services.memory.memory import MemoryRecord

# Phase 4 (Real-World Communication): the deterministic yes/no this
# project's own confirmation gate is checked against - never a model
# call, so this resolves identically under Full Brain and Half Brain,
# the same discipline ``PendingClarification.select_candidate`` already
# holds itself to for its own, different question. Deliberately a fixed,
# small vocabulary rather than free-text understanding: a consequential
# action is exactly the place a guess is least acceptable.
_CONFIRM_WORDS: tuple[str, ...] = (
    "yes", "yeah", "yep", "yup", "sure", "confirm", "confirmed",
    "go ahead", "do it", "send it", "make the call", "please do",
)
_CANCEL_WORDS: tuple[str, ...] = (
    "no", "nope", "nah", "cancel", "stop", "don't", "do not",
    "never mind", "nevermind", "forget it",
)


def _try_resolve_confirmation(
    request, pending: PendingConfirmation, sessions: ActivitySessions, agent_key: str
) -> "TaskResult | None":
    """Resolve one pending consequential-action confirmation against the
    very next remark - deterministic, never a Brain call. Returns None
    (leave it pending) when the reply is neither a clear yes nor a clear
    no, the identical "did not match, may be saying something else
    first" fall-through ``guided_interaction_response`` already applies
    to an unresolved clarification.
    """
    reply = " ".join(str(request.task.description or "").split()).strip().lower()
    if not reply:
        return None

    if any(word in reply for word in _CANCEL_WORDS):
        sessions.clear_confirmation(agent_key)
        return TaskResult.success(
            request.task, request.own_name,
            f"Okay, I won't {pending.operation} {pending.party}.",
            payload={
                "handled_by": "communication", "operation": pending.operation,
                "party": pending.party, "confirmed": False,
            },
        )

    if any(word in reply for word in _CONFIRM_WORDS):
        sessions.clear_confirmation(agent_key)
        from src.manager.capabilities import _communication_call, _communication_send

        if pending.operation == "call":
            return _communication_call(request, pending.party)
        return _communication_send(request, pending.party, pending.content)

    return None


def guided_interaction_response(request) -> "TaskResult | None":
    """The one entry point ``open_conversation`` calls. Returns a
    TaskResult when this remark was handled as part of an in-progress or
    newly-starting clarification/activity/confirmation; None when it was
    not, meaning the caller should compose an ordinary conversational
    reply instead.
    """
    sessions: "ActivitySessions | None" = request.activity_sessions
    ops: "ActivityBrainOps | None" = request.activity_brain
    if sessions is None:
        return None
    agent_key = request.own_name

    # Phase 4: checked first - a pending consequential-action
    # confirmation is the most time-sensitive in-progress state (a real
    # send or call is one word away), and must never be silently
    # abandoned in favour of resolving an unrelated clarification first.
    confirmation = sessions.pending_confirmation(agent_key)
    if confirmation is not None:
        resolved = _try_resolve_confirmation(request, confirmation, sessions, agent_key)
        if resolved is not None:
            return resolved
        # Neither a clear yes nor a clear no - leave it pending, the
        # person may simply be saying something else first; fall through
        # exactly as an unresolved clarification already does below.

    pending = sessions.pending_clarification(agent_key)
    if pending is not None:
        resolved = _try_resolve_clarification(request, pending, sessions, agent_key)
        if resolved is not None:
            return resolved
        # Did not match a candidate - leave it pending, the person may
        # simply be saying something else first; fall through to check
        # for an active activity, then to ordinary conversation.

    active = sessions.activity(agent_key)
    if active is not None and ops is not None:
        continued = _continue_guided_activity(request, active, ops, sessions, agent_key)
        if continued is not None:
            return continued

    if ops is None:
        return None

    notes = tuple(request.recognition.notes()) + tuple(_conversation_notes(request))

    ambiguity = ops.discern_ambiguity(request.task, notes)
    if ambiguity.is_decided and ambiguity.reason.strip() and len(ambiguity.steps) >= 2:
        clarification = PendingClarification(question=ambiguity.reason, candidates=ambiguity.steps)
        sessions.raise_clarification(agent_key, clarification)
        return TaskResult.success(
            request.task, request.own_name, clarification.question,
            payload={"handled_by": "open_conversation", "clarification": "raised", "candidates": list(clarification.candidates)},
        )

    start = ops.discern_activity_start(request.task, notes)
    if start.is_decided and start.reason.strip():
        topic = start.reason
        first_question = ops.compose_activity_question(topic, 1, ())
        if first_question.is_decided and first_question.reason.strip():
            state = GuidedActivityState(topic=topic, question_number=1, current_question=first_question.reason)
            sessions.start_activity(agent_key, state)
            return TaskResult.success(
                request.task, request.own_name, first_question.reason,
                payload={"handled_by": "open_conversation", "activity": "started", "topic": topic},
            )
        # Wanted to start, but no question could be composed - honest
        # fall-through to ordinary conversation rather than starting a
        # session with nothing to ask.

    return None


def _conversation_notes(request) -> Sequence[str]:
    """The same recent-conversation evidence ``open_conversation`` itself
    already gathers - imported at call time to avoid a circular import
    with ``capabilities.py``, which is this module's own only caller."""
    from src.manager.capabilities import _recent_conversation_notes

    return _recent_conversation_notes(request)


def _try_resolve_clarification(request, pending: PendingClarification, sessions, agent_key: str) -> "TaskResult | None":
    chosen = pending.select_candidate(request.task.description)
    if chosen is None:
        return None

    sessions.clear_clarification(agent_key)

    if request.retain is not None:
        request.retain(
            MemoryRecord(
                content=f"Resolved: {chosen} (had been unsure between {', '.join(pending.candidates)}).",
                kind=MemoryCategory.USER_CONTEXT.value,
                tags={"resolved_ambiguity"},
                context={
                    "source": "conversation",
                    "resolved_value": chosen,
                    "candidates": list(pending.candidates),
                },
            )
        )

    # Supersede: an existing record naming one of the *other* candidates
    # must no longer be treated as current truth alongside the one just
    # chosen. General on purpose - keyed on the candidate strings
    # actually offered, never a fact-type-specific lookup.
    if request.forget is not None and request.recall is not None:
        others = tuple(c for c in pending.candidates if c != chosen)
        for record in request.recall(limit=50):
            if any(other.lower() in record.content.lower() for other in others):
                if chosen.lower() not in record.content.lower():
                    request.forget(record.id)

    return TaskResult.success(
        request.task, request.own_name,
        f"Got it - I'll go with {chosen} from now on.",
        payload={"handled_by": "open_conversation", "clarification": "resolved", "chosen": chosen},
    )


def _continue_guided_activity(
    request, active: GuidedActivityState, ops: ActivityBrainOps, sessions: ActivitySessions, agent_key: str
) -> "TaskResult | None":
    verdict = ops.evaluate_activity_answer(
        active.topic, active.current_question, request.task.description, active.history
    )
    if not verdict.evaluated:
        # Half Brain, or a provider failure mid-session - honest
        # fall-through to ordinary conversation rather than a fabricated
        # grade. The session stays active; the next genuine answer gets
        # another chance to be graded.
        return None

    if verdict.wants_to_stop:
        sessions.end_activity(agent_key)
        _retain_progress_if_any(request, active)
        return TaskResult.success(
            request.task, request.own_name,
            f"No problem - we'll stop there. {active.score_line.capitalize()}.",
            payload={"handled_by": "open_conversation", "activity": "ended", "score": active.score_line},
        )

    if verdict.correct == "yes":
        active.correct_count += 1
    elif verdict.correct == "partial":
        active.partial_count += 1
    else:
        active.incorrect_count += 1
    active.history = active.history + (
        f"Q: {active.current_question} -- A: {request.task.description} -- graded {verdict.correct or 'ungraded'}",
    )

    next_number = active.question_number + 1
    next_question = ops.compose_activity_question(active.topic, next_number, active.history)
    if not next_question.is_decided or not next_question.reason.strip():
        sessions.end_activity(agent_key)
        _retain_progress_if_any(request, active)
        reply = f"{verdict.feedback} That's all I have for now - {active.score_line}.".strip()
        return TaskResult.success(
            request.task, request.own_name, reply,
            payload={"handled_by": "open_conversation", "activity": "ended", "score": active.score_line},
        )

    active.question_number = next_number
    active.current_question = next_question.reason
    sessions.start_activity(agent_key, active)

    reply = f"{verdict.feedback} {next_question.reason}".strip()
    return TaskResult.success(
        request.task, request.own_name, reply,
        payload={"handled_by": "open_conversation", "activity": "continuing", "score": active.score_line},
    )


def _retain_progress_if_any(request, active: GuidedActivityState) -> None:
    if request.retain is None or active.answered_count == 0:
        return
    request.retain(
        MemoryRecord(
            content=f"Practiced {active.topic}: {active.score_line}.",
            kind=MemoryCategory.USER_CONTEXT.value,
            tags={"learning_progress"},
            context={"source": "conversation", "topic": active.topic},
        )
    )
