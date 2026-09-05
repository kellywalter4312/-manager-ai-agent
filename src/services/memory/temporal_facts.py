"""Dates as authoritative facts, so derived information stays current
without anyone having to restate it.

The distinction this module exists to enforce, stated the way this
project's own memory modules already state theirs:

    stored     an authoritative date - a birth date, a graduation date
    derived    whatever follows from that date and today - an age, a
               countdown, a next occurrence

A birth date does not change; an age does, every year, without anyone
saying so. Storing "I am 20" is storing a fact that silently goes stale
the day it stops being true. Storing the birth date and computing the age
*at the moment it is asked* is the only version of "current" that does
not require the user to keep correcting the record - the same argument
``ToolResult.observed_at`` and ``LiveInfoResult.retrieved_at`` already
make about the difference between a fact and the moment it was true.

**Deterministic, and knows nothing about Memory, Brain or any agent.**
Two halves, both pure functions: parsing free-text dates into
``datetime.date``, and computing an age/next-occurrence/countdown from
one. Nothing here reads a MemoryRecord, writes one, or decides what
should be retained - see ``user_facts.py`` for where these functions are
actually put to use.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

_MONTHS: dict[str, int] = {
    name: index
    for index, name in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}
# Common abbreviations, mapped to the same index.
_MONTHS.update(
    {name[:3]: index for name, index in list(_MONTHS.items())}
)

_MONTH_NAME_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "September 27, 2006" / "Sep 27 2006"
_MONTH_DAY_YEAR = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
# "27 September 2006" / "27th of September 2006"
_DAY_MONTH_YEAR = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>{_MONTH_NAME_PATTERN})\.?,?\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
# "2006-09-27"
_ISO_DATE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
# "27/09/2006" or "09/27/2006" - day-first assumed (the project's own
# established convention is UTC/ISO-first elsewhere; this is read as
# day/month/year, the more common convention outside the US, and is a
# clearly secondary fallback to the two named-month forms above, which are
# unambiguous and preferred whenever present).
_SLASH_DATE = re.compile(r"\b(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})\b")
# "May 2028" - no day named, the ordinary way a completion or graduation
# date is actually spoken. The 1st of the month stands in for the missing
# day, which is precise enough for a countdown measured in months.
_MONTH_YEAR = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_PATTERN})\.?\s+(?P<year>\d{{4}})\b", re.IGNORECASE
)
# "March 24" / "April 19th" - no year at all, the ordinary way a
# recurring date (a birthday, an anniversary) is spoken when the year is
# unknown or beside the point. Deliberately tried only by parse_month_day
# below, after a full year-bearing date has already had its chance - so
# "March 24, 2008" is read as a full date, never mistaken for a bare one.
_MONTH_DAY = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)


def parse_natural_date(text: str) -> date | None:
    """The first recognisable calendar date in ``text``, or None.

    Deterministic pattern matching, the same discipline every other
    extractor in this project already follows - no model, no guessing,
    so this behaves identically in Half Brain and is exactly as testable
    without one. A date this cannot parse is not silently invented; the
    caller gets None and treats the statement as ordinary text instead.
    """
    candidates = str(text or "")
    for pattern in (_MONTH_DAY_YEAR, _DAY_MONTH_YEAR, _ISO_DATE, _SLASH_DATE):
        match = pattern.search(candidates)
        if match is None:
            continue
        groups = match.groupdict()
        month_raw = groups["month"]
        month = _MONTHS[month_raw.lower()] if not month_raw.isdigit() else int(month_raw)
        try:
            return date(int(groups["year"]), month, int(groups["day"]))
        except ValueError:
            continue

    # No day named - "May 2028" - the ordinary way a completion date is
    # actually spoken. Tried last, since a full date above is always more
    # precise when present.
    match = _MONTH_YEAR.search(candidates)
    if match is not None:
        groups = match.groupdict()
        try:
            return date(int(groups["year"]), _MONTHS[groups["month"].lower()], 1)
        except ValueError:
            return None
    return None


def parse_month_day(text: str) -> tuple[int, int] | None:
    """``(month, day)`` from free text with no year required or assumed -
    "March 24", "April 19th" - the ordinary way a recurring personal date
    is spoken when the year is unknown. Stage 12 closure: a birthday
    without a birth year must still work correctly as a recurring
    birthday, and this is what makes that representable at all -
    ``parse_natural_date`` above always requires a year.

    Deliberately does not fall back to this when a full date is present:
    a caller wanting "know the year if it's there, else just month/day"
    tries ``parse_natural_date`` first and only calls this on that
    result being None, so "March 24, 2008" is never split into a
    year-less answer merely because this pattern also happens to match
    its first two tokens.
    """
    match = _MONTH_DAY.search(str(text or ""))
    if match is None:
        return None
    groups = match.groupdict()
    month = _MONTHS[groups["month"].lower()]
    day = int(groups["day"])
    try:
        date(2000, month, day)  # 2000 is a leap year - validates Feb 29 too
    except ValueError:
        return None
    return month, day


def utc_today() -> date:
    """The default "today". Injectable so tests can control time."""
    return datetime.now(timezone.utc).date()


def compute_age(born: date, today: date | None = None) -> int:
    """Whole years since ``born``, as of ``today``.

    The one honest way to answer "how old" without the answer going
    stale the moment it is stored: computed fresh from the authoritative
    date every time it is asked, never cached as a number.
    """
    as_of = today if today is not None else utc_today()
    years = as_of.year - born.year
    if (as_of.month, as_of.day) < (born.month, born.day):
        years -= 1
    return years


def next_occurrence(month: int, day: int, today: date | None = None) -> date:
    """The next calendar date carrying ``month``/``day`` - a birthday, an
    anniversary, any yearly-recurring date - on or after ``today``.

    Falls back to the 28th for a "29 February" recurrence in a
    non-leap year, the same forgiving rule most calendars already use,
    rather than raising over an ordinary, expected edge case.
    """
    as_of = today if today is not None else utc_today()

    def _date_in(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 28)

    candidate = _date_in(as_of.year)
    if candidate < as_of:
        candidate = _date_in(as_of.year + 1)
    return candidate


def days_until(target_month: int, target_day: int, today: date | None = None) -> int:
    """Days remaining until the next occurrence of ``target_month``/``target_day``."""
    as_of = today if today is not None else utc_today()
    return (next_occurrence(target_month, target_day, as_of) - as_of).days


def days_between(target: date, today: date | None = None) -> int:
    """Days from ``today`` to a specific, non-recurring ``target`` date -
    a graduation date, a project deadline - which future does not repeat
    annually the way a birthday does. Negative once the date has passed."""
    as_of = today if today is not None else utc_today()
    return (target - as_of).days
