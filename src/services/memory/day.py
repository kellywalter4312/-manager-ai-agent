"""Which calendar day a memory belongs to.

A read-time lens over durable memory, and deliberately nothing more. It stores
nothing, writes nothing, and changes nothing it is given.

The idea it implements:

    ACTIVE DAY -> work happens -> DAY ENDS -> HISTORY -> NEW DAWN

and the reason it needs no machinery to do so: **a record becomes historical
because the clock moved, not because anything ran.** There is no midnight job,
no rollover, no archive step, no second store. A record's day is derived from
its ``created_at`` every time it is asked for, so at 00:00 exactly nothing
happens - the answer to "what is today?" simply changes.

That is what makes every awkward operational case fall out for free. The
machine can be off across midnight, the heartbeat can miss a week, a tick can
straddle the boundary - none of them needs catch-up logic, because none of them
was ever going to be asked to do anything.

Two clocks, kept apart on purpose:

    storage    UTC, always - MemoryRecord.created_at is never reinterpreted
    calendar   a configured IANA zone - which human day that instant fell on

Conflating them is the bug this module exists to avoid. 23:30 UTC on the 21st
is already 00:30 on the 22nd in Lagos, and it is the *person's* day that
decides what "today" means to them.

What this module must never become: a brain. It filters by timestamp, which is
arithmetic. It does not summarise, rank by importance, decide what matters, or
interpret a question - all of which are reasoning, and belong upstairs.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Callable, Iterable

from src.services.memory.memory import Memory, MemoryRecord, utc_now
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Phase 11 final gate: the default is the machine's own local time, never one
# person's city. "Africa/Lagos" used to be written here (and in Settings), so
# every installation reckoned days in Lagos unless told otherwise - and without
# the tzdata package no other named zone could be configured at all.
LOCAL_TIMEZONE = "local"
DEFAULT_TIMEZONE = LOCAL_TIMEZONE


class SystemLocalTime(tzinfo):
    """The operating system's own local time, asked per instant - so it is
    exact wherever the machine is, daylight saving included, with no time zone
    database. The name a person configures is still preferred when given."""

    def fromutc(self, dt: datetime) -> datetime:
        seconds = (dt.replace(tzinfo=timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
        offset = timedelta(seconds=_time.localtime(seconds).tm_gmtoff)
        return (dt + offset).replace(tzinfo=self)

    def utcoffset(self, dt: "datetime | None") -> timedelta:
        if dt is None:
            return timedelta(seconds=_time.localtime().tm_gmtoff)
        try:
            seconds = _time.mktime(dt.replace(tzinfo=None).timetuple())
        except (OverflowError, ValueError):
            return timedelta(seconds=_time.localtime().tm_gmtoff)
        return timedelta(seconds=_time.localtime(seconds).tm_gmtoff)

    def dst(self, dt: "datetime | None") -> timedelta:
        return timedelta(0)

    def tzname(self, dt: "datetime | None") -> str:
        return LOCAL_TIMEZONE

    def __repr__(self) -> str:
        return "SystemLocalTime()"

# Zones this project can still reckon correctly when no IANA database is
# installed. Every entry must be a zone with a **fixed** offset and no daylight
# saving, because a fixed offset is then exact rather than an approximation.
#
# The reason this table exists at all: Windows ships no tz database, and
# zoneinfo therefore resolves nothing unless the `tzdata` package is present.
# Africa/Lagos (WAT) has been UTC+01:00 with no DST throughout the modern era,
# so falling back to a fixed +01:00 loses nothing.
#
# A zone that observes DST must never be added here. Half the year would be
# silently wrong, which is worse than refusing to start.
FIXED_OFFSET_ZONES: dict[str, timedelta] = {
    "utc": timedelta(0),
    "africa/lagos": timedelta(hours=1),
}

# How many records to ask for before filtering by day. PersistentMemory.recall
# returns the best `limit` matches across *all* time, so asking for five and
# then discarding yesterday's can leave fewer than five. Over-fetching then
# trimming is the simple fix; the alternative was adding temporal parameters to
# the Memory contract, which would have broken every implementation and every
# test double that implements it.
OVERFETCH_FACTOR = 8
MAX_OVERFETCH = 500


class TimezoneError(ValueError):
    """Raised when a configured timezone cannot be used.

    A ValueError so it lands with the rest of the configuration failures: the
    same category as an unknown provider or an unknown router name, reported at
    startup rather than surfacing later as days that quietly fall on the wrong
    side of midnight.
    """


def resolve_timezone(name: str) -> tzinfo:
    """Turn a configured timezone name into something datetimes can use.

    Tries the real IANA database first, which is correct everywhere it exists
    and handles DST properly. Falls back only for zones listed above as
    fixed-offset, and refuses anything else rather than guessing - a wrong day
    boundary is the kind of error nobody notices until a memory shows up on the
    wrong date.
    """
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise TimezoneError("timezone must not be empty")
    if cleaned.lower() == LOCAL_TIMEZONE:
        return SystemLocalTime()

    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:  # pragma: no cover - zoneinfo is stdlib from 3.9
        ZoneInfo = None  # type: ignore[assignment]
        ZoneInfoNotFoundError = Exception  # type: ignore[misc,assignment]

    if ZoneInfo is not None:
        try:
            return ZoneInfo(cleaned)
        except ZoneInfoNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            raise TimezoneError(f"invalid timezone {cleaned!r}: {exc}") from exc

    offset = FIXED_OFFSET_ZONES.get(cleaned.lower())
    if offset is None:
        raise TimezoneError(
            f"timezone {cleaned!r} could not be resolved. No IANA time zone "
            "database is available - install the 'tzdata' package to use "
            "arbitrary zones. Zones usable without it: "
            f"{', '.join(sorted(FIXED_OFFSET_ZONES))}."
        )

    logger.warning(
        "no IANA time zone database found; using a fixed %s offset for '%s'. "
        "This is exact for that zone because it observes no daylight saving, "
        "but installing 'tzdata' is the general fix.",
        offset, cleaned,
    )
    return timezone(offset, cleaned)


@dataclass(frozen=True)
class Calendar:
    """How one agent reckons days: a zone, and a clock.

    Frozen and tiny. It answers "what day is it?" and "what day was that?" and
    nothing else - it holds no records and knows nothing about memory.

    The clock is injected for the reason every clock in this project is: a test
    that had to wait for midnight to check midnight behaviour would never be
    written.
    """

    zone: tzinfo = timezone.utc
    clock: Callable[[], datetime] = utc_now

    @classmethod
    def utc(cls) -> "Calendar":
        """The neutral default, for callers that were given no configuration."""
        return cls()

    @classmethod
    def configured(cls, name: str, clock: Callable[[], datetime] = utc_now) -> "Calendar":
        return cls(zone=resolve_timezone(name), clock=clock)

    def today(self) -> date:
        """The current local calendar date."""
        return self.local_date(self.clock())

    def now(self) -> datetime:
        """The current local date and time.

        ``astimezone`` for the same reason ``local_date`` uses it: the zone
        decides the offset at this instant rather than a fixed one applied by
        hand, so this stays correct across a daylight-saving change too.
        """
        return self.clock().astimezone(self.zone)

    def local_date(self, moment: datetime) -> date:
        """Which local calendar date an instant fell on.

        ``astimezone`` rather than adding an offset by hand. That is what makes
        this correct across a daylight-saving change: the zone decides what the
        offset was *at that instant*, where naive arithmetic would apply
        today's offset to a timestamp from the other side of a transition.

        A naive datetime is read as UTC. Every timestamp this project creates
        is timezone-aware, so this only covers a record hand-built without one,
        and reading it as UTC matches how the rest of the system stores time.
        """
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(self.zone).date()

    def day_of(self, record: MemoryRecord) -> date:
        """Which local day a record belongs to. Never changes for a record."""
        return self.local_date(record.created_at)

    def is_today(self, record: MemoryRecord) -> bool:
        return self.day_of(record) == self.today()


class DayView:
    """Today's memories, and yesterday's, over one durable store.

    A lens, not a store. It holds a Memory it did not create, asks it the same
    questions any other caller would, and filters the answers by date. It has
    no ``remember``, no file, and no state - if it ever needs one of those,
    something has gone wrong.

    One view wraps one Memory, so the Manager's view sees only the Manager's
    records and a specialist's sees only its own. The separation is inherited
    rather than re-implemented.
    """

    def __init__(self, memory: Memory, calendar: Calendar | None = None) -> None:
        self._memory = memory
        self._calendar = calendar or Calendar.utc()

    @property
    def calendar(self) -> Calendar:
        return self._calendar

    @property
    def memory(self) -> Memory:
        return self._memory

    def __repr__(self) -> str:
        return f"<DayView zone={self._calendar.zone} over={self._memory!r}>"

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def today(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        """Records belonging to the current local day, best match first."""
        return self.on(self._calendar.today(), query, kind=kind, tags=tags, limit=limit)

    def on(
        self,
        day: date,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        """Records belonging to one specific local day."""
        return self._filtered(
            lambda record: self._calendar.day_of(record) == day,
            query, kind=kind, tags=tags, limit=limit,
        )

    def history(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
        before: date | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Records from days earlier than ``before`` (today by default).

        Deliberately a separate method rather than a flag on today(). History
        has to be *asked for*: yesterday's conclusions drifting silently into
        today's reasoning is the exact failure this whole layer exists to
        prevent, and a default that included them would reintroduce it.
        """
        boundary = before or self._calendar.today()
        return self._filtered(
            lambda record: self._calendar.day_of(record) < boundary,
            query, kind=kind, tags=tags, limit=limit,
        )

    def days(self, limit: int = MAX_OVERFETCH) -> tuple[date, ...]:
        """Every local day this store holds records for, most recent first.

        Read-only and non-interpreting: a list of dates, not a judgement about
        which of them mattered.
        """
        seen = {self._calendar.day_of(record) for record in self._memory.recall(limit=limit)}
        return tuple(sorted(seen, reverse=True))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _filtered(self, keep, query, *, kind, tags, limit) -> tuple[MemoryRecord, ...]:
        """Ask the store, then drop what belongs to another day.

        Over-fetches because recall() ranks across all of time: the best five
        matches overall may include none from today. Asking for more and
        trimming keeps the Memory contract untouched, which was the point.

        Ordering is whatever recall() returned, unchanged - filtering removes
        records, it never reorders them. Re-sorting here would be this module
        deciding what is most relevant, which is not its job.
        """
        if limit <= 0:
            # Left to the store to reject, so the error text and type stay
            # identical whether or not a day view is in the way.
            return self._memory.recall(query, kind=kind, tags=tags, limit=limit)

        wanted = min(max(limit * OVERFETCH_FACTOR, limit), MAX_OVERFETCH)
        candidates = self._memory.recall(query, kind=kind, tags=tags, limit=wanted)
        return tuple(record for record in candidates if keep(record))[:limit]
