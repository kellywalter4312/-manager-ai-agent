"""The calendar-event contract - named, recurring calendar dates
(Christmas, Valentine's Day, Easter), never a raw day/week/month/year
transition (see ``src/services/memory/temporal_events.py`` for that -
and for why "New Year" deliberately lives there instead of here).

The same abstraction move ``src/providers/live_info.py``/
``communication.py`` already made: anything that wants to know "is
today a named calendar occasion" depends on this interface, never a
hardcoded list scattered through capability or heartbeat code.

**Why "default" here is a genuine implementation, not an empty
placeholder - the identical reasoning ``DefaultCelebrationProvider``'s
own docstring already gives.** Unlike a live web search or a real phone
connection, there is no key, no network and no external service a
handful of fixed-rule holidays could be missing - New Year's Day,
Christmas and Valentine's Day are the same fixed month/day every year,
and Easter's date, while it moves, follows a fully deterministic,
long-published rule (the "Computus" algorithm) with no external data
source at all. So ``DefaultCalendarEventProvider`` is what an
unconfigured installation gets, exactly as ``DefaultCelebrationProvider``
already is - never "null" standing in for "not configured yet".

**Why this is deliberately not a giant holiday list.** This project's
own brief refuses "build a giant hardcoded list of every holiday" -
correctly: a genuinely complete public-holiday calendar is locale-
specific (a bank holiday in one country is an ordinary Tuesday in
another) and belongs to a real external data source this project does
not have, the same honest position ``WikipediaLiveInfoProvider``
already takes about "current information" versus everything Stage 14
could have tried to fabricate instead. ``NullCalendarEventProvider``
exists for exactly the same reason ``NullLiveInfoProvider`` does: an
honest "nothing is known" for whoever does not want even the small,
unambiguous default set, and the seam a real locale-aware provider
would fill in without this file, or anything that calls it, changing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CalendarEvent:
    """One named, recurring calendar occasion, true of one date.

    The same presentation-agnostic shape ``TemporalEvent`` already
    keeps: a kind and a date, never a sentence and never a reference to
    a person. ``event_type`` is a plain label ("christmas",
    "valentines_day", "easter", ...) a caller can key a celebration
    reaction on - there is no per-holiday branch anywhere downstream
    that would need to grow as a provider adds more.
    """

    event_type: str
    label: str
    occurred_on: date

    def __post_init__(self) -> None:
        if not str(self.event_type).strip():
            raise ValueError("CalendarEvent.event_type must not be empty")
        if not str(self.label).strip():
            raise ValueError("CalendarEvent.label must not be empty")
        if not isinstance(self.occurred_on, date):
            raise ValueError("CalendarEvent.occurred_on must be a date")


class CalendarEventProvider(ABC):
    """A source of named, recurring calendar occasions.

    One narrow method, the same shape every other provider in this
    project already proved cheap to implement and cheap to replace. A
    null implementation, the shipped default, and a future real
    locale-aware backend all satisfy this identically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "default"."""

    @abstractmethod
    def events_on(self, day: date) -> tuple[CalendarEvent, ...]:
        """Named calendar occasions that fall on ``day``.

        Must never raise for an ordinary, valid ``date``; a day with
        nothing notable returns an empty tuple, never an invented
        occasion. Never returns a raw day/week/month/year transition -
        see this module's own docstring for where that lives instead.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
