"""Stage 12 closure - time itself as a source of meaningful events.

Distinct from ``temporal_facts.py`` beside it: that module derives
information *from a stored fact* (an age from a birth date). This module
derives nothing from anything stored - it asks a single question of the
calendar alone: "does today begin a new day, week, month or year?" No
Memory, no Brain, no agent is read or needed to answer it, which is
exactly what lets it stay correct in Half Brain (see this project's own
stance: date arithmetic is not reasoning).

**Deliberately not a scheduler.** This computes whether a transition
*is true of a given day* - a pure question with a pure answer. Whether
that fact has already been reported once (so a heartbeat ticking hourly
does not repeat itself) is somebody else's job - the existing
``StateStore`` dedup bucket, reused exactly as it already is for
proactive milestones (see ``scripts/heartbeat.py``). No second temporal
or scheduling system is introduced here.

**"New Year" lives here, not in the calendar-events provider.** Both
this stage's brief and ordinary language treat "a new year began" and
"today is New Year's Day" as the same fact, and firing both would be the
identical event twice under two names. This module owns it, because a
year rolling over is fundamentally a *transition*, the same kind of fact
as a new month - see ``src/providers/calendar_events.py``'s own
docstring for the other half of this reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TemporalEvent:
    """One calendar transition, true of exactly one day.

    Presentation-agnostic and content-free by design, the same shape
    ``CalendarEvent``/``CelebrationReaction`` already keep to: what kind
    of thing this is, and when - never a sentence, never who it is for.
    ``event_type`` is a plain, generic label ("new_month", "new_year",
    ...) - there is no per-value branch anywhere that could grow with a
    calendar's worth of special cases.
    """

    event_type: str
    label: str
    occurred_on: date

    def __post_init__(self) -> None:
        if not str(self.event_type).strip():
            raise ValueError("TemporalEvent.event_type must not be empty")
        if not isinstance(self.occurred_on, date):
            raise ValueError("TemporalEvent.occurred_on must be a date")


def due_temporal_transitions(today: date) -> tuple[TemporalEvent, ...]:
    """Which generic calendar transitions are true of ``today``.

    Pure date arithmetic, nothing else - nothing here reads a clock
    twice, reads Memory, or asks a Brain anything. A caller that wants
    "has this already been reported" applies the existing StateStore
    dedup bucket to the result, exactly as it already does for a
    proactive milestone.

    Deliberately returns every transition true of the day, including
    "new_day" (true every day by definition) and "new_week" (true every
    Monday, the ISO convention) - detection is generic and unopinionated
    about what is *worth celebrating*; that curation happens where the
    result is consumed (today: only new_month/new_year feed a
    celebration reaction, matching this stage's own brief examples -
    "It's September" and "Happy New Year!" - never every single day or
    week, which "do not make every event flashy" plainly rules out).
    """
    events: list[TemporalEvent] = [
        TemporalEvent(event_type="new_day", label="a new day", occurred_on=today)
    ]
    if today.weekday() == 0:  # Monday - the ISO 8601 week-start convention
        events.append(TemporalEvent(event_type="new_week", label="a new week", occurred_on=today))
    if today.day == 1:
        events.append(TemporalEvent(event_type="new_month", label=today.strftime("%B"), occurred_on=today))
    if today.month == 1 and today.day == 1:
        events.append(TemporalEvent(event_type="new_year", label=str(today.year), occurred_on=today))
    return tuple(events)
