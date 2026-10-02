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

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable

from src.manager.memory_policy import MemoryCategory
from src.services.memory.calendar_items import all_current_calendar_items
from src.services.memory.milestones import due_recurring_dates
from src.services.memory.temporal_events import due_temporal_transitions
from src.services.memory.temporal_facts import utc_today

logger = logging.getLogger(__name__)

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
    source: str  # the registered source that produced it: "personal", "temporal", "calendar",
                 # "calendar_item", "reminder" - or any source registered later ("condition", GM-02)
    record: "MemoryRecord | None" = None


@dataclass(frozen=True)
class ProactiveContext:
    """What every proactive source is asked with (D-2). ``settings`` lets a
    source that keeps its own declared state (conditions, GM-02) find it."""

    manager: "ManagerAgent"
    today: date
    calendar_events: "CalendarEventProvider | None" = None
    now: "datetime | None" = None
    settings: "Any | None" = None


ProactiveSource = Callable[[ProactiveContext], "Iterable[ProactiveEvent]"]

# D-2 (GM campaign): what can make something proactively due is an open,
# ordered registry - not five blocks in one function. The five sources this
# project has always had are registered below, in their original order, so the
# events every existing caller receives are unchanged; a new kind of
# "something is due" (a condition over observations, GM-02) registers itself
# and joins every caller - the heartbeat, a live server - with nothing here
# changing.
_SOURCES: "dict[str, ProactiveSource]" = {}


def register_proactive_source(name: str, source: ProactiveSource) -> None:
    """Add (or replace) a named source. Registration order is the order
    events are reported in."""
    _SOURCES[str(name)] = source


def proactive_sources() -> "tuple[str, ...]":
    return tuple(_SOURCES)


def due_proactive_events(
    manager: "ManagerAgent",
    *,
    today: date | None = None,
    calendar_events: "CalendarEventProvider | None" = None,
    now: "datetime | None" = None,
    settings: "Any | None" = None,
) -> tuple[ProactiveEvent, ...]:
    """Every event genuinely due, from every registered source, as one
    uniform list - so a single caller (the heartbeat, a live server) can
    dedup, select a real experience and present over all of them
    identically.

    Day/week transitions are deliberately excluded here even though
    ``due_temporal_transitions`` detects them - see that function's own
    docstring for why only new_month/new_year are curated as
    celebration-worthy by default; a caller that wants raw day/week
    awareness for some other purpose calls that function directly.

    A source that fails is skipped with a log line rather than costing every
    other source its events - the same "one failing part never silences the
    rest" rule the ecosystem's arrival listeners follow.
    """
    context = ProactiveContext(
        manager=manager, today=today if today is not None else utc_today(),
        calendar_events=calendar_events, now=now, settings=settings,
    )
    events: list[ProactiveEvent] = []
    for name, source in list(_SOURCES.items()):
        try:
            events.extend(source(context))
        except Exception:  # noqa: BLE001 - one source never silences the rest
            logger.exception("proactive source '%s' failed", name)
    return tuple(events)


def _personal_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    events = []
    for record in due_proactive_milestones(context.manager, today=context.today):
        record_context = record.context or {}
        event_type = str(record_context.get("fact_type") or record_context.get("milestone_type") or record.kind)
        events.append(
            ProactiveEvent(
                event_type=event_type,
                label=event_type.replace("_", " "),
                dedup_key=f"proactive:{record.id}:{context.today.isoformat()}",
                source="personal",
                record=record,
            )
        )
    return events


def _temporal_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    events = []
    for transition in due_temporal_transitions(context.today):
        if transition.event_type not in ("new_month", "new_year"):
            continue
        events.append(
            ProactiveEvent(
                event_type=transition.event_type,
                label=transition.label,
                dedup_key=f"temporal:{transition.event_type}:{context.today.isoformat()}",
                source="temporal",
            )
        )
    return events


def _calendar_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    if context.calendar_events is None:
        return []
    return [
        ProactiveEvent(
            event_type=occasion.event_type,
            label=occasion.label,
            dedup_key=f"calendar:{occasion.event_type}:{context.today.isoformat()}",
            source="calendar",
        )
        for occasion in context.calendar_events.events_on(context.today)
    ]


def _calendar_item_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    if context.manager.memory is None:
        return []
    events = []
    for item in all_current_calendar_items(recall=context.manager.memory.recall):
        if item.recurrence.is_recurring or item.start_date != context.today:
            continue
        events.append(
            ProactiveEvent(
                event_type=item.topic_key or "calendar_item",
                label=item.title,
                dedup_key=f"calendar_item:{item.item_key}:{context.today.isoformat()}",
                source="calendar_item",
            )
        )
    return events


def _reminder_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    # Pre-Stage-9 Campaign 6: a pending reminder whose moment has arrived.
    # Only when a caller knows the time (``now``) - a day-only check never
    # guesses which reminders of today are already due.
    if context.now is None or context.manager.memory is None:
        return []
    events = []
    for record in context.manager.memory.recall(kind="user_task", tags={"reminder"}, limit=500):
        record_context = record.context or {}
        if record_context.get("status") != "pending" or not record_context.get("due_at"):
            continue
        try:
            due_at = datetime.fromisoformat(str(record_context["due_at"]))
        except ValueError:
            continue
        if due_at > context.now:
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
    return events


for _name, _source in (
    ("personal", _personal_source),
    ("temporal", _temporal_source),
    ("calendar", _calendar_source),
    ("calendar_item", _calendar_item_source),
    ("reminder", _reminder_source),
):
    register_proactive_source(_name, _source)
