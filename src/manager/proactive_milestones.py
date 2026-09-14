"""Stage 12 closure - genuine proactive milestone surfacing.

The audit's own finding, closed here: ``proactive_status``/``is_mentionable``
(``src/services/memory/milestones.py``) were correctly implemented and
correctly unit-tested, but nothing anywhere actually consulted them - a
stored "silent" or "snooze" preference had no observable effect because
nothing ever asked whether a milestone was due to be mentioned, and a
birthday could only ever be answered when asked, never surfaced.

**Read-only, and reading only what already exists.** This module writes
nothing, stores nothing, and introduces no second memory system: it asks
an agent's own, already-governed Memory - through the identical
``.recall()`` every capability handler in ``src/manager/capabilities.py``
already uses - which dated records currently match today, then hands that
list to ``due_recurring_dates`` (this stage's own generic date-matching
and preference filter) to decide which ones are actually due.

**Never fabricates.** A person with no stored date of birth and no
milestones gets an empty tuple, every time - never an invented "happy
birthday" for a date nobody ever gave Alfred.

**No Brain, no second router, no new orchestration.** This is a plain
function taking a ``ManagerAgent`` and returning data; nothing here
reasons, decides who handles anything, or writes a word a person reads -
that composition (what a proactive milestone *means*, in Alfred's own
voice) is deliberately left to whatever caller actually surfaces the
result, exactly as ``milestone_recognition``'s own ``_celebrate`` already
composes a milestone's wording through the Brain rather than a canned
string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from src.manager.memory_policy import MemoryCategory
from src.services.memory.calendar_items import all_current_calendar_items
from src.services.memory.milestones import due_recurring_dates
from src.services.memory.temporal_events import due_temporal_transitions
from src.services.memory.temporal_facts import utc_today

if TYPE_CHECKING:
    from src.manager.manager import ManagerAgent
    from src.providers.calendar_events import CalendarEventProvider
    from src.services.memory.memory import MemoryRecord


def due_proactive_milestones(
    manager: "ManagerAgent", *, today: date | None = None
) -> tuple["MemoryRecord", ...]:
    """Which of this Manager's own governed milestones and dated facts
    are due to be proactively mentioned today, per each record's own
    stored preference.

    ``manager.memory`` only - never a specialist's, and never a second
    store; a specialist's own milestones are that specialist's own
    business, exactly as every other governed boundary in this project
    already keeps. A Manager with no memory configured (a bare test
    construction) gets an honest empty tuple, the same shape every other
    optional collaborator in this project already returns for "nothing
    reaches this".

    ``today`` is injectable, matching every other clock in this project
    (``utc_now``, ``utc_today``) - defaulted to the real day so a caller
    that does not care gets correct, current behaviour for free.
    """
    if manager.memory is None:
        return ()
    resolved_today = today if today is not None else utc_today()
    milestones = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=1000)
    facts = manager.memory.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=1000)
    return due_recurring_dates(milestones + facts, today=resolved_today)


@dataclass(frozen=True)
class ProactiveEvent:
    """One thing genuinely due to be proactively surfaced today, from
    any of the four sources this project's own generalized-event
    architecture combines - never a new concept of its own:

        personal        a governed Memory record (a milestone, a
                        birthday) - ``due_proactive_milestones`` above,
                        unchanged
        temporal        a raw calendar transition (new month, new
                        year) - ``src/services/memory/temporal_events.py``
        calendar        a named recurring occasion (Christmas, Easter,
                        ...) - whatever ``CalendarEventProvider`` was
                        configured
        calendar_item   a genuinely singular, dated occasion the user
                        taught through the generalized calendar
                        (``src/services/memory/calendar_items.py``) -
                        General Time/Event Awareness checkpoint. Only a
                        non-recurring item counts: a routine weekly
                        block (a class, a standing meeting) firing
                        "due" every single occurrence would be exactly
                        the "hardcode what counts as significant"
                        mistake this project's own architecture
                        refuses - recurrence *shape* is a real, generic
                        signal available from the data itself, never a
                        judgement about the item's subject.

    ``dedup_key`` is what a caller applies the existing StateStore
    dedup bucket to (see ``scripts/heartbeat.py``) - stable per source
    and per day, so the identical event is never reported twice within
    one occurrence merely because a heartbeat ticked again.
    """

    event_type: str
    label: str
    dedup_key: str
    source: str  # "personal" | "temporal" | "calendar" | "calendar_item" | "reminder"
    record: "MemoryRecord | None" = None


def due_proactive_events(
    manager: "ManagerAgent",
    *,
    today: date | None = None,
    calendar_events: "CalendarEventProvider | None" = None,
    now: "datetime | None" = None,
) -> tuple[ProactiveEvent, ...]:
    """Every event genuinely due today, from all four sources, as one
    uniform list - the aggregation this Stage 12 closure (and the
    General Time/Event Awareness checkpoint's own calendar_item
    addition) adds, and nothing more: each item below is produced by an
    existing, unchanged function, normalised into one shape so a single
    caller (the heartbeat, a live server) can dedup, select a real
    experience and present over all of them identically.

    Day/week transitions are deliberately excluded here even though
    ``due_temporal_transitions`` detects them - see that function's own
    docstring for why only new_month/new_year are curated as
    celebration-worthy by default; a caller that wants raw day/week
    awareness for some other purpose calls that function directly.
    """
    resolved_today = today if today is not None else utc_today()
    events: list[ProactiveEvent] = []

    for record in due_proactive_milestones(manager, today=resolved_today):
        context = record.context or {}
        event_type = str(context.get("fact_type") or context.get("milestone_type") or record.kind)
        events.append(
            ProactiveEvent(
                event_type=event_type,
                label=event_type.replace("_", " "),
                dedup_key=f"proactive:{record.id}:{resolved_today.isoformat()}",
                source="personal",
                record=record,
            )
        )

    for transition in due_temporal_transitions(resolved_today):
        if transition.event_type not in ("new_month", "new_year"):
            continue
        events.append(
            ProactiveEvent(
                event_type=transition.event_type,
                label=transition.label,
                dedup_key=f"temporal:{transition.event_type}:{resolved_today.isoformat()}",
                source="temporal",
            )
        )

    if calendar_events is not None:
        for occasion in calendar_events.events_on(resolved_today):
            events.append(
                ProactiveEvent(
                    event_type=occasion.event_type,
                    label=occasion.label,
                    dedup_key=f"calendar:{occasion.event_type}:{resolved_today.isoformat()}",
                    source="calendar",
                )
            )

    if manager.memory is not None:
        for item in all_current_calendar_items(recall=manager.memory.recall):
            if item.recurrence.is_recurring or item.start_date != resolved_today:
                continue
            events.append(
                ProactiveEvent(
                    event_type=item.topic_key or "calendar_item",
                    label=item.title,
                    dedup_key=f"calendar_item:{item.item_key}:{resolved_today.isoformat()}",
                    source="calendar_item",
                )
            )

    # Pre-Stage-9 Campaign 6: a pending reminder whose moment has arrived.
    # Only when a caller knows the time (``now``) - a day-only check never
    # guesses which reminders of today are already due.
    if now is not None and manager.memory is not None:
        for record in manager.memory.recall(kind="user_task", tags={"reminder"}, limit=500):
            context = record.context or {}
            if context.get("status") != "pending" or not context.get("due_at"):
                continue
            try:
                due_at = datetime.fromisoformat(str(context["due_at"]))
            except ValueError:
                continue
            if due_at > now:
                continue
            events.append(
                ProactiveEvent(
                    event_type="reminder",
                    label=record.content,
                    dedup_key=f"reminder:{record.id}:{due_at.isoformat()}",
                    source="reminder",
                    record=record,
                )
            )

    return tuple(events)
