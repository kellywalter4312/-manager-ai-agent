"""The one real, generic ``CalendarEventProvider`` this project ships:
deterministic, no network, no vendor SDK, no locale data file.

The same role ``DefaultCelebrationProvider`` already plays for
presentation reactions: a genuinely working default, not a placeholder.
Three fixed-date occasions and one computed one - New Year's Day is
deliberately absent, because it is a raw year transition, already
covered by ``src/services/memory/temporal_events.py`` (see that
module's own docstring for the reconciliation).

**Easter is computed, never looked up.** The date of Easter Sunday
follows a fully determined, long-published rule (the "Computus") - the
"anonymous Gregorian algorithm" (Meeus/Jones/Butcher) implemented below
is pure arithmetic over the year number, the same kind of pure function
``temporal_facts.py``'s own age/next-occurrence calculations already
are. No table of Easter dates is stored anywhere, and none needs to be:
a future year needs no data update.

**Why "public holidays" more broadly are still not here.** A bank
holiday is locale-specific in a way none of the four occasions below
are - see ``calendar_events.py``'s own docstring for why that stays an
honest gap this default does not pretend to close.
"""

from __future__ import annotations

from datetime import date

from src.providers.calendar_events import CalendarEvent, CalendarEventProvider


def easter_sunday(year: int) -> date:
    """The date of Easter Sunday in ``year`` (Gregorian calendar).

    The "anonymous Gregorian algorithm" (also called the Meeus/Jones/
    Butcher algorithm for the Computus) - a closed-form, deterministic
    calculation, not a lookup. Verified against well-known reference
    dates (2024-03-31, 2025-04-20, 2026-04-05) in this stage's own test
    suite.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


class DefaultCalendarEventProvider(CalendarEventProvider):
    @property
    def name(self) -> str:
        return "default"

    def events_on(self, day: date) -> tuple[CalendarEvent, ...]:
        events: list[CalendarEvent] = []
        if (day.month, day.day) == (12, 25):
            events.append(CalendarEvent(event_type="christmas", label="Christmas", occurred_on=day))
        if (day.month, day.day) == (2, 14):
            events.append(CalendarEvent(event_type="valentines_day", label="Valentine's Day", occurred_on=day))
        if day == easter_sunday(day.year):
            events.append(CalendarEvent(event_type="easter", label="Easter", occurred_on=day))
        return tuple(events)
