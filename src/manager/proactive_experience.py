"""General Time/Event Awareness + Proactive Visual Experience checkpoint
- the real orchestration step between "an event is genuinely due" and
"a real, history-aware experience is selected and persisted".

**The two independent paths this checkpoint keeps apart, stated once,
here, so nothing downstream has to re-derive it:**

    direct animation/anime request    an ordinary, occasion-free
                                       creative ask - media_production
                                       in ``src/manager/capabilities.py``,
                                       entirely unchanged and untouched
                                       by this module
    contextual/proactive experience   this module - reached only when
                                       ``due_proactive_events`` (see
                                       ``proactive_milestones.py``) says
                                       something is genuinely due, never
                                       from a user's own creative request

Nothing in this module can be reached by asking Alfred to make an
animation; nothing in ``media_production`` knows this module exists.

**Selection, never invention.** Every experience this module ever
persists came from a real ``CelebrationProvider.select()`` call - never
a fabricated combination of an avatar/environment/overlay action this
project's own configured provider did not actually produce. A provider
reporting nothing available (``enabled=False``, most commonly because
celebration is turned off) produces no experience at all, honestly.

**History-aware, without a new store.** ``experience_history_for``
(``src/services/memory/experience_history.py``) reads this agent's own
already-governed Memory for what was genuinely selected before; this
module uses that to actively try for a reaction different from the one
most recently used, bounded and honest - if the real, available space is
exhausted, it settles for the provider's own last answer rather than
fabricating an alternative that does not exist.

**Idempotent by construction.** Calling ``evaluate_and_persist_experiences``
again for an occurrence that already has an experience returns the
identical, already-persisted record - never re-selects, never
re-persists. Safe to call from a heartbeat tick, a live snapshot read, or
both, in either order, any number of times.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from src.manager.proactive_milestones import ProactiveEvent, due_proactive_events
from src.services.identity.personality import personality_for
from src.services.memory.experience_history import (
    ExperienceCandidate,
    ExperienceRecord,
    build_experience_record,
    current_experience_for_event,
    delivered_candidate,
    experience_history_for,
    pending_experiences,
)
from src.services.memory.milestones import resolve_celebration_intensity
from src.services.memory.temporal_facts import utc_today

if TYPE_CHECKING:
    from src.manager.manager import ManagerAgent
    from src.providers.calendar_events import CalendarEventProvider
    from src.providers.celebration import CelebrationProvider
    from src.services.identity.personality import AgentPersonality

# How many alternate occurrence indices the selector will try before
# honestly settling for whatever the provider's own last answer was -
# bounds the search to a real, proportionate amount of work rather than
# looping forever chasing variety a small provider genuinely cannot
# offer.
_MAX_VARIETY_ATTEMPTS = 12


def select_experience(
    event: ProactiveEvent,
    *,
    agent_key: str,
    personality: "AgentPersonality | None",
    intensity: str,
    provider: "CelebrationProvider",
    recall,
) -> "ExperienceCandidate | None":
    """A real, history-aware experience for one genuinely due event.

    Reads this (agent, event type) pair's own real experience history to
    compute a genuine occurrence count and to know which reaction was
    most recently used, then actively tries nearby occurrence indices
    (never fabricated ones - each is still a real ``provider.select()``
    call) until one differs from the last-used reaction, or the search
    is honestly exhausted. Returns None only when intensity is off or
    the provider itself reports nothing available - never invented.
    """
    if intensity == "off":
        return None

    history = experience_history_for(agent_key, event.event_type, recall=recall)
    occurrence = len(history)
    last_reaction_id = history[0].reaction_id if history else None

    reaction = None
    for attempt in range(_MAX_VARIETY_ATTEMPTS):
        proposed = provider.select(
            milestone_type=event.event_type,
            agent_key=agent_key,
            personality=personality,
            intensity=intensity,
            occurrence=occurrence + attempt,
        )
        if not proposed.enabled:
            return None
        reaction = proposed
        if last_reaction_id is None or proposed.reaction_id != last_reaction_id:
            break

    if reaction is None:
        return None

    return ExperienceCandidate(
        event_type=event.event_type,
        dedup_key=event.dedup_key,
        agent_key=agent_key,
        reaction_id=reaction.reaction_id,
        avatar_action=reaction.avatar_action,
        environment_action=reaction.environment_action,
        overlay=reaction.overlay,
        intensity=reaction.intensity,
        duration_ms=reaction.duration_ms,
        delivered=False,
    )


def evaluate_and_persist_experiences(
    manager: "ManagerAgent",
    *,
    today: date | None = None,
    calendar_events: "CalendarEventProvider | None" = None,
) -> "tuple[ExperienceRecord, ...]":
    """Which events are genuinely due today, and - for each - the real
    experience already selected for it, selecting and persisting one
    (through this agent's own governed Memory) for any that do not yet
    have one.

    ``manager.memory``/``manager.own_skills`` only - never a
    specialist's, the identical scoping ``due_proactive_milestones``
    already enforces (see requirement 11: agent independence). A
    Manager with no memory, no registered agent, or no configured
    celebration provider returns an honest empty tuple rather than
    guessing at any of them.
    """
    if manager.memory is None or manager.own_skills is None:
        return ()
    provider = getattr(manager.own_skills, "_celebration_provider", None)
    if provider is None:
        return ()

    resolved_today = today if today is not None else utc_today()
    due = due_proactive_events(manager, today=resolved_today, calendar_events=calendar_events)
    if not due:
        return ()

    agent_key = manager.own_skills.name
    personality = personality_for(manager.own_skills)
    intensity = resolve_celebration_intensity(manager.memory.recall)

    results: list[ExperienceRecord] = []
    for event in due:
        existing = current_experience_for_event(event.dedup_key, recall=manager.memory.recall)
        if existing is not None:
            results.append(existing)
            continue

        candidate = select_experience(
            event, agent_key=agent_key, personality=personality, intensity=intensity,
            provider=provider, recall=manager.memory.recall,
        )
        if candidate is None:
            continue

        verdict = manager.retain(build_experience_record(candidate, noted_by=agent_key))
        if not verdict.allowed:
            continue
        resolved = current_experience_for_event(event.dedup_key, recall=manager.memory.recall)
        if resolved is not None:
            results.append(resolved)

    return tuple(results)


def deliver_experience(manager: "ManagerAgent", record_id: str) -> "ExperienceRecord | None":
    """Mark one pending experience delivered - a real, governed write
    (a new record sharing the same dedup_key, the identical append-only
    lifecycle every experience record already has), called only once a
    real frontend has genuinely presented it. Returns the delivered
    record, or None when ``record_id`` does not name a real, currently
    pending experience for this agent - never a fabricated success.
    """
    if manager.memory is None:
        return None
    pending = {e.record_id: e for e in pending_experiences(recall=manager.memory.recall)}
    target = pending.get(record_id)
    if target is None:
        return None
    verdict = manager.retain(
        build_experience_record(delivered_candidate(target), noted_by=target.agent_key)
    )
    if not verdict.allowed:
        return None
    return current_experience_for_event(target.dedup_key, recall=manager.memory.recall)
