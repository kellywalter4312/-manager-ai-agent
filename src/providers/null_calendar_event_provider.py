"""A calendar-event provider that knows no occasions at all.

The honest "nothing is known" default, the same role
``NullLiveInfoProvider``/``NullCommunicationProvider`` already play -
never a placeholder standing in for "not implemented yet", but a real,
complete implementation of "this installation has no calendar
awareness configured". Available for anyone who wants zero calendar
behaviour; ``DefaultCalendarEventProvider`` (a genuinely working
implementation, not this one) is what an unconfigured installation
actually gets - see that module's own docstring for why.
"""

from __future__ import annotations

from datetime import date

from src.providers.calendar_events import CalendarEvent, CalendarEventProvider


class NullCalendarEventProvider(CalendarEventProvider):
    @property
    def name(self) -> str:
        return "null"

    def events_on(self, day: date) -> tuple[CalendarEvent, ...]:
        return ()
