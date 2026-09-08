"""Generalized Personal Organization / Calendar - real structured state,
never free text that merely reads like a schedule.

**What this closes.** ``personal_context.py`` already lets a person teach
Alfred a topic-keyed statement - "this is my new IT schedule: Mon-Fri
9-3" - and resolves which statement about a topic is current. That is
real, but it is still prose: nothing about it can answer "what do I have
at 2pm on Tuesday" or "am I free Friday morning" without re-parsing text
every time. This module is what a schedule-shaped statement, once
interpreted, actually *means*: a real date/time/duration/recurrence with
a title and a lifecycle - genuinely structured state, related to (never
merged with) the personal-context statement it may have come from.

**The generalization.** A ``CalendarItemCandidate`` is never matched
against a fixed vocabulary of activities. It has a free-text ``title``
(whatever the user said this is), an optional ``start_date``, an optional
``start_time``/``end_time``, and a ``Recurrence`` - three genuinely
generic temporal shapes (once, weekly-on-these-weekdays) rather than a
"school schedule" or "work schedule" special case. A future kind of
organizational item needs no new code here; it is the same shape with a
different title.

**Two-level lifecycle, without mutation.** ``MemoryRecord`` is frozen, so
nothing here is ever edited in place - every write is a new record. Two
independent identities, both tags (the same topic-tag-plus-recall(limit=1)
"latest wins" mechanism ``personal_context.py`` already established and
proved):

    item_key    one real-world calendar item, stable across its own
                edits ("move my dentist appointment to 4pm" writes a new
                record sharing the same item_key; recall(tags={that
                key}, limit=1) always answers this one item's current
                state - date, time, status - honestly, with every prior
                version still sitting in the store, never deleted).
    series_key  every item created together in one teaching event (a
                five-day timetable taught in one sentence becomes five
                records sharing one series_key). Replacing a whole taught
                schedule ("this is my new IT schedule") mints a *new*
                series_key for the same topic_key; recall(tags={that
                topic}, limit=1) finds the newest series, and every item
                in an older series is honestly superseded - never shown
                as current beside its replacement, never deleted either.

**Deterministic floor, Brain-assisted ceiling** - the identical shape
``personal_context.py``'s own module docstring already states, and the
identical reason: everything below is Half-Brain-safe (no model
consulted); the genuinely open-ended cases (an irregular, multi-entry
timetable, an ambiguous phrasing) are the capability's own Tier 2, in
``src/manager/capabilities.py``, which asks a Brain only when nothing
here matched and validates its answer against real parsing before ever
writing it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable

from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import normalize_topic_key
from src.services.memory.temporal_facts import parse_month_day, parse_natural_date

CALENDAR_ITEM_KIND = "calendar_item"
CALENDAR_ITEM_TAG = "calendar_item"

STATUS_SCHEDULED = "scheduled"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"

FREQUENCY_ONCE = "once"
FREQUENCY_WEEKLY = "weekly"

_Recall = Callable[..., "Iterable[MemoryRecord]"]


def _item_tag(item_key: str) -> str:
    return "calendar_item_key:" + "_".join(str(item_key).lower().split())


def _series_tag(series_key: str) -> str:
    return "calendar_series:" + "_".join(str(series_key).lower().split())


def _topic_tag(topic_key: str) -> str:
    return "calendar_topic:" + "_".join(str(topic_key).lower().split())


# ---------------------------------------------------------------------------
# Structured shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recurrence:
    """A real, generic recurrence rule - never a "school schedule" or any
    other named-activity special case. ``days_of_week`` is 0=Monday..
    6=Sunday, populated only for FREQUENCY_WEEKLY. ``until`` is an
    optional real boundary date after which this recurrence no longer
    applies - a generic temporal-context field, not a semester-specific
    one."""

    frequency: str = FREQUENCY_ONCE
    days_of_week: tuple[int, ...] = ()
    until: "date | None" = None

    def to_dict(self) -> dict:
        return {
            "frequency": self.frequency,
            "days_of_week": list(self.days_of_week),
            "until": self.until.isoformat() if self.until else None,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Recurrence":
        data = data or {}
        until_raw = data.get("until")
        until = date.fromisoformat(until_raw) if until_raw else None
        return cls(
            frequency=str(data.get("frequency") or FREQUENCY_ONCE),
            days_of_week=tuple(int(d) for d in (data.get("days_of_week") or ())),
            until=until,
        )

    @property
    def is_recurring(self) -> bool:
        return self.frequency == FREQUENCY_WEEKLY and bool(self.days_of_week)


@dataclass(frozen=True)
class CalendarItemCandidate:
    """One recognised calendar statement, not yet written - the neutral
    shape every extractor below returns and the capability offers to
    governed memory, the identical "candidates, not memories" stance
    ``personal_context.py`` already holds to."""

    title: str
    start_date: "date | None" = None
    start_time: "time | None" = None
    end_time: "time | None" = None
    recurrence: Recurrence = field(default_factory=Recurrence)
    status: str = STATUS_SCHEDULED
    notes: str = ""

    def __post_init__(self) -> None:
        if not str(self.title).strip():
            raise ValueError("CalendarItemCandidate.title must not be blank")


@dataclass(frozen=True)
class CalendarItemRecord:
    """One resolved, current calendar item, read back from Memory - the
    shape a caller (a capability, Control Center, the snapshot) actually
    wants, rather than a raw ``MemoryRecord`` whose ``context`` dict it
    would otherwise have to unpack itself."""

    item_id: str
    item_key: str
    series_key: str
    topic_key: str
    title: str
    start_date: "date | None"
    start_time: "time | None"
    end_time: "time | None"
    recurrence: Recurrence
    status: str
    notes: str
    noted_by: str
    created_at: "object"
    record_id: str
    related_task_id: "str | None" = None
    related_personal_context_topic: "str | None" = None

    @property
    def duration_minutes(self) -> "int | None":
        if self.start_time is None or self.end_time is None:
            return None
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return (end - start) if end >= start else (end + 24 * 60 - start)

    @property
    def temporal_description(self) -> str:
        """A plain, pre-rendered description of when this item falls -
        a date, a recurring weekday set, or both - shared by
        ``describe()`` here and by Control Center's own listing, so
        "when is this" is never described two different ways."""
        when = self.start_date.isoformat() if self.start_date else ""
        if self.recurrence.is_recurring:
            names = ", ".join(_WEEKDAY_LABELS[d] for d in self.recurrence.days_of_week)
            when = f"every {names}" if not when else f"{when}, every {names}"
        span = ""
        if self.start_time is not None:
            span = self.start_time.strftime("%H:%M")
            if self.end_time is not None:
                span += f"-{self.end_time.strftime('%H:%M')}"
        pieces = [p for p in (when, span) if p]
        return ", ".join(pieces)

    def describe(self) -> str:
        detail = f" ({self.temporal_description})" if self.temporal_description else ""
        marker = {"scheduled": " ", "cancelled": "x", "completed": "v"}.get(self.status, " ")
        return f"[{marker}] {self.title}{detail}"


# ---------------------------------------------------------------------------
# Deterministic extraction - recognised by shape, never by subject.
# ---------------------------------------------------------------------------

WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_WEEKDAY_LABELS: dict[int, str] = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}
_WEEKDAY_PATTERN = "|".join(sorted(WEEKDAY_NAMES, key=len, reverse=True))
_WEEKDAY_TOKEN = re.compile(rf"\b({_WEEKDAY_PATTERN})s?\b", re.IGNORECASE)

_RELATIVE_DAYS: dict[str, int] = {"today": 0, "tomorrow": 1}


def find_weekdays(text: str) -> "tuple[tuple[int, ...], tuple[int, int]] | None":
    """Every weekday named in ``text``, and the span they occupy - a
    range ("Mon-Fri", "Monday to Friday") when exactly two tokens are
    joined by a range connector, otherwise the plain set of whichever
    individual weekdays were named ("Monday and Wednesday", "Tue, Thu").
    Recognised by grammatical shape only; there is no fixed list of
    "school days" or "work days" anywhere in this function.
    """
    tokens = [
        (m.start(), m.end(), WEEKDAY_NAMES[m.group(1).lower()])
        for m in _WEEKDAY_TOKEN.finditer(text)
    ]
    if not tokens:
        return None
    if len(tokens) == 2:
        (s1, e1, d1), (s2, e2, d2) = tokens
        between = text[e1:s2]
        if re.fullmatch(r"\s*(?:-|to|through|thru)\s*", between, re.IGNORECASE):
            days = tuple(range(d1, d2 + 1)) if d1 <= d2 else tuple(
                list(range(d1, 7)) + list(range(0, d2 + 1))
            )
            return days, (s1, e2)
    days = tuple(sorted({d for _, _, d in tokens}))
    return days, (tokens[0][0], tokens[-1][1])


def _to_time(hour: str, minute: "str | None", meridiem: "str | None") -> time:
    h = int(hour)
    m = int(minute) if minute else 0
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "pm" and h != 12:
            h += 12
        if meridiem == "am" and h == 12:
            h = 0
    return time(hour=h % 24, minute=m)


_TIME_RANGE = re.compile(
    r"(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<ap1>am|pm)?\s*(?:-|to|–)\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>am|pm)?",
    re.IGNORECASE,
)
_TIME_SINGLE = re.compile(
    r"(?:\bat\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?",
    re.IGNORECASE,
)


def _resolve_bare_range_meridiem(h1: int, h2: int) -> tuple[int, int]:
    """The honest, documented convention this module uses when a time
    range names neither am nor pm ("9-3", "2-4") - genuinely ambiguous
    natural language, resolved the same deliberate way every other
    deterministic extractor in this project accepts a real, useful
    heuristic over a claim of complete understanding (see
    ``tasks.py``'s own ``match_tasks_by_topic`` docstring for the
    identical stance): a range that counts *down* in 12-hour terms
    ("9-3") is read as crossing noon (9am-3pm, the ordinary way a
    daytime span is spoken); a range that counts up is read as entirely
    within one half of the day - the afternoon for an hour named 1-7
    ("2-4" -> 2pm-4pm), the morning otherwise ("9-11" -> 9am-11am).
    """
    if h2 < h1:
        return h1 % 12, (h2 % 12) + 12
    if 1 <= h1 <= 7:
        base = h1 % 12
        return base + 12, (h2 % 12) + 12
    return h1 % 12, h2 % 12


def find_time_range(text: str) -> "tuple[time, time | None, tuple[int, int]] | None":
    """The first time or time-range named in ``text`` - "9-3", "9am-3pm",
    "10:00-11:30", "at 2pm", "2:30pm" - and the span it occupies."""
    match = _TIME_RANGE.search(text)
    if match is not None:
        groups = match.groupdict()
        h1, h2 = int(groups["h1"]), int(groups["h2"])
        ap1, ap2 = groups["ap1"], groups["ap2"]
        if ap1 is None and ap2 is None and h1 <= 12 and h2 <= 12:
            h1, h2 = _resolve_bare_range_meridiem(h1, h2)
            start = time(hour=h1 % 24, minute=int(groups["m1"] or 0))
            end = time(hour=h2 % 24, minute=int(groups["m2"] or 0))
        else:
            # One or both sides named am/pm explicitly - honour it
            # verbatim, and let an unmarked side inherit the other's
            # meridiem only when doing so keeps the range moving forward
            # in time (never invented beyond what was actually said).
            if ap1 is None and ap2 is not None:
                ap1 = ap2 if h1 >= h2 else ap2
            if ap2 is None and ap1 is not None:
                ap2 = ap1
            start = _to_time(groups["h1"], groups["m1"], ap1)
            end = _to_time(groups["h2"], groups["m2"], ap2)
        return start, end, match.span()

    match = _TIME_SINGLE.search(text)
    if match is not None:
        groups = match.groupdict()
        start = _to_time(groups["h"], groups["m"], groups["ap"])
        return start, None, match.span()
    return None


def _match_time_range_at_start(text: str) -> "tuple[time, time | None, str] | None":
    """Like ``find_time_range``, anchored to the very start of ``text`` -
    used when a weekday phrase is immediately followed by its own time
    ("Mon-Fri 9-3 IT"), so a time mentioned later in an unrelated part
    of the sentence is never mistaken for this item's own."""
    stripped = text.lstrip()
    offset = len(text) - len(stripped)
    for pattern in (_TIME_RANGE, _TIME_SINGLE):
        match = pattern.match(stripped)
        if match is None:
            continue
        groups = match.groupdict()
        if "h1" in groups:
            h1, h2 = int(groups["h1"]), int(groups["h2"])
            ap1, ap2 = groups["ap1"], groups["ap2"]
            if ap1 is None and ap2 is None and h1 <= 12 and h2 <= 12:
                h1, h2 = _resolve_bare_range_meridiem(h1, h2)
                start = time(hour=h1 % 24, minute=int(groups["m1"] or 0))
                end = time(hour=h2 % 24, minute=int(groups["m2"] or 0))
            else:
                if ap1 is None and ap2 is not None:
                    ap1 = ap2
                if ap2 is None and ap1 is not None:
                    ap2 = ap1
                start = _to_time(groups["h1"], groups["m1"], ap1)
                end = _to_time(groups["h2"], groups["m2"], ap2)
            return start, end, stripped[match.end():].strip()
        start = _to_time(groups["h"], groups["m"], groups["ap"])
        return start, None, stripped[match.end():].strip()
    return None


def next_weekday_occurrence(weekday: int, *, today: date) -> date:
    """The next real calendar date, on or after ``today``, that falls on
    ``weekday`` (0=Monday..6=Sunday) - "on Friday" with no explicit date
    names the *next* Friday, never today's date reused regardless of
    what day it actually is."""
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def parse_calendar_date(phrase: str, *, today: "date | None" = None) -> "date | None":
    """A real calendar date from free text - a relative word
    ("today"/"tomorrow"), a bare weekday name (the next real occurrence
    of it), or any date shape ``temporal_facts.py`` already recognises
    (with or without a year). Never a guess: unrecognised text returns
    None, exactly as every deterministic extractor in this project
    already does."""
    from src.services.memory.temporal_facts import utc_today

    as_of = today if today is not None else utc_today()
    cleaned = " ".join(str(phrase or "").strip().lower().split())
    if not cleaned:
        return None
    if cleaned in _RELATIVE_DAYS:
        return as_of + timedelta(days=_RELATIVE_DAYS[cleaned])

    full = parse_natural_date(phrase)
    if full is not None:
        return full

    weekday_match = re.fullmatch(rf"({_WEEKDAY_PATTERN})s?", cleaned, re.IGNORECASE)
    if weekday_match is not None:
        return next_weekday_occurrence(WEEKDAY_NAMES[weekday_match.group(1).lower()], today=as_of)

    month_day = parse_month_day(phrase)
    if month_day is not None:
        month, day = month_day
        try:
            candidate = date(as_of.year, month, day)
        except ValueError:
            return None
        if candidate < as_of:
            try:
                candidate = date(as_of.year + 1, month, day)
            except ValueError:
                return None
        return candidate
    return None


_LEADING_CONNECTOR = re.compile(
    r"^(?:i have|i've got|i have got|there'?s|there is|:|-|,)\s*", re.IGNORECASE,
)
# A single leading word immediately followed by a colon ("event: X",
# "reminder: X", "appointment: X") is a generic label shape, not a
# vocabulary of known item types - stripped the same way "record this
# for X:" already strips its own leading label in capture.py.
_LEADING_LABEL = re.compile(r"^[a-z][a-z '\-]*:\s*", re.IGNORECASE)


def _clean_title(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip(" ,.:;!?")
    cleaned = _LEADING_CONNECTOR.sub("", cleaned).strip(" ,.:;!?")
    cleaned = _LEADING_LABEL.sub("", cleaned).strip(" ,.:;!?")
    return cleaned


_MAX_TITLE_CHARS = 120


def _bounded(text: str) -> str:
    return text if len(text) <= _MAX_TITLE_CHARS else text[:_MAX_TITLE_CHARS].rstrip() + " ..."


# --- ADD: a single, dated (or bare-weekday-dated) item ---------------------

_ON_MARKER = re.compile(
    r"\b(?:i have|i've got|i have got|there'?s|there is|add(?:ing)?|schedule[d]?)\s+"
    r"(?:an?\s+|my\s+)?(?P<title>[^.!?]+?)\s+on\s+(?P<rest>[^.!?]+)",
    re.IGNORECASE,
)


def _extract_dated_add(text: str, *, today: "date | None" = None) -> "CalendarItemCandidate | None":
    match = _ON_MARKER.search(text)
    if match is None:
        return None
    title = _clean_title(match.group("title"))
    if not title:
        return None
    rest = match.group("rest")
    time_split = re.search(r"\bat\s+(?P<time>.+)$", rest, re.IGNORECASE)
    date_phrase = rest[: time_split.start()].strip() if time_split else rest.strip()
    time_phrase = time_split.group("time").strip() if time_split else ""

    when = parse_calendar_date(date_phrase, today=today)
    if when is None:
        return None

    start_time = end_time = None
    if time_phrase:
        found = find_time_range(time_phrase)
        if found is not None:
            start_time, end_time, _ = found

    return CalendarItemCandidate(
        title=_bounded(title), start_date=when, start_time=start_time, end_time=end_time,
    )


# --- ADD: a weekly-recurring item, anchored on a weekday phrase ------------

_RECURRING_LEAD_OK = re.compile(r"^(?:every|on|each)?\s*$", re.IGNORECASE)


def _recurring_lead_is_acceptable(before: str) -> bool:
    """Whether the text preceding a found weekday phrase is a shape this
    module accepts as "the weekday phrase is what this statement is
    about" - either near-empty/"every"/"on"/"each" (the weekday opens
    the statement), or ending in a colon (a generic "label: value" shape
    - "this is my new IT schedule: Mon-Fri 9am-3pm" - the identical
    convention capture.py's own "record this for X: Y" already uses).
    Anything else (an unrelated sentence that merely mentions a weekday
    mid-clause, "I visited Paris and Monday was rainy") is correctly
    refused."""
    return bool(_RECURRING_LEAD_OK.match(before)) or before.endswith(":")


_LABEL_QUALIFIER = re.compile(
    r"^(?:(?:this is|here is|here'?s)\s+my\s+(?:new\s+|updated\s+|current\s+|latest\s+)?"
    r"|my\s+(?:new\s+|updated\s+)?)",
    re.IGNORECASE,
)


def _label_title(before: str) -> str:
    """A readable title from the label text preceding a "label: value"
    shaped weekday statement ("this is my new IT schedule:" -> "IT
    schedule") - the identical leading-qualifier stripping
    ``personal_context.normalize_topic_key`` already applies, kept
    case-preserving here since this becomes a displayed title rather
    than a lookup key."""
    text = before.rstrip(":").strip()
    return _LABEL_QUALIFIER.sub("", text).strip()


def _extract_recurring_add(text: str) -> "CalendarItemCandidate | None":
    found = find_weekdays(text)
    if found is None:
        return None
    days, (start, end) = found
    before = text[:start].strip()
    if not _recurring_lead_is_acceptable(before):
        return None
    after = text[end:]

    timed = _match_time_range_at_start(after)
    if timed is not None:
        start_time, end_time, remainder = timed
    else:
        start_time = end_time = None
        remainder = after.strip()

    title = _clean_title(remainder)
    if not title and before.endswith(":"):
        # "this is my new IT schedule: Mon-Fri 9am-3pm" - the value
        # names only the time block; the label itself is what this
        # recurring item is about.
        title = _label_title(before)
    if not title:
        return None

    return CalendarItemCandidate(
        title=_bounded(title), start_time=start_time, end_time=end_time,
        recurrence=Recurrence(frequency=FREQUENCY_WEEKLY, days_of_week=days),
    )


def extract_calendar_item(
    text: str, *, today: "date | None" = None
) -> "CalendarItemCandidate | None":
    """The single deterministic ADD candidate for ``text`` - a
    weekday-anchored recurring item first (so "Mon-Fri 9-3 IT" is never
    mistaken for a one-off dated item naming no date at all), then a
    dated single item ("I have a dentist appointment on Friday at
    3pm"). ``today`` is injectable, the same reason every clock in this
    project is (``day.py``'s own ``Calendar``) - a test resolving "on
    Friday" must not depend on which real day it happens to run."""
    return _extract_recurring_add(text) or _extract_dated_add(text, today=today)


def calendar_add_intent(text: str) -> bool:
    return extract_calendar_item(text) is not None


# --- CANCEL ------------------------------------------------------------

_CANCEL_MARKERS: tuple[str, ...] = ("cancel my", "cancel the", "cancel")


def _after_marker(text: str, markers: tuple[str, ...]) -> str:
    """Whatever follows the first ``markers`` entry found in ``text``,
    matched at a real word boundary - never a bare substring search.
    Found live, this checkpoint: a bare-word marker like "cancel"
    plainly substring-matches "noise-*cancel*ling", misclaiming an
    unrelated research request as a cancellation."""
    lowered = str(text or "").lower()
    for marker in markers:
        match = re.search(r"\b" + re.escape(marker) + r"\b", lowered)
        if match is None:
            continue
        remainder = text[match.end():].strip(" ,.:;?\t\n")
        return remainder
    return ""


def calendar_cancel_intent(text: str) -> bool:
    return bool(_after_marker(text, _CANCEL_MARKERS))


def extract_cancel_target(text: str) -> str:
    return _after_marker(text, _CANCEL_MARKERS)


# --- RESCHEDULE ----------------------------------------------------------

_RESCHEDULE_MARKERS: tuple[str, ...] = (
    "move my", "move the", "reschedule my", "reschedule the", "reschedule",
)


def calendar_reschedule_intent(text: str) -> bool:
    remainder = _after_marker(text, _RESCHEDULE_MARKERS)
    return bool(remainder) and bool(re.search(r"\bto\b", remainder, re.IGNORECASE))


def extract_reschedule(
    text: str, *, today: "date | None" = None
) -> "tuple[str, date | None, time | None, time | None] | None":
    """``(target_title, new_date, new_start_time, new_end_time)`` for an
    explicit reschedule request, or None. The same honest, first-" to "
    limitation ``tasks.py``'s own ``extract_task_update`` already
    accepts and documents. ``today`` is injectable, the same reason
    ``extract_calendar_item``'s own is."""
    remainder = _after_marker(text, _RESCHEDULE_MARKERS)
    if not remainder:
        return None
    match = re.search(r"\bto\b", remainder, re.IGNORECASE)
    if match is None:
        return None
    target = remainder[: match.start()].strip(" ,.:;?\t\n")
    new_phrase = remainder[match.end():].strip(" ,.:;?\t\n")
    if not target or not new_phrase:
        return None

    time_split = re.search(r"\bat\s+(?P<time>.+)$", new_phrase, re.IGNORECASE)
    date_phrase = new_phrase[: time_split.start()].strip() if time_split else new_phrase
    time_phrase = time_split.group("time").strip() if time_split else ""

    new_date = parse_calendar_date(date_phrase, today=today)
    new_start = new_end = None
    if time_phrase:
        found = find_time_range(time_phrase)
        if found is not None:
            new_start, new_end, _ = found
    elif new_date is None:
        # No "at <time>" and no recognisable date either - maybe the
        # whole phrase was only ever a time ("move my meeting to 4pm").
        found = find_time_range(date_phrase)
        if found is not None:
            new_start, new_end, _ = found
            date_phrase = ""

    if new_date is None and new_start is None:
        return None
    return target, new_date, new_start, new_end


# --- RETRIEVE ------------------------------------------------------------

_LIST_MARKERS: tuple[str, ...] = (
    "what's on my calendar", "whats on my calendar", "what is on my calendar",
    "what do i have coming up", "what's coming up", "whats coming up",
    "what's scheduled", "whats scheduled", "am i free",
)


def calendar_retrieve_intent(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in _LIST_MARKERS)


def extract_period_query(text: str) -> str:
    """Whatever period phrase follows the retrieval marker ("this week",
    "tomorrow", "on Friday") - "" for a bare listing, the identical ""
    -> "everything" shape ``extract_task_query`` already uses."""
    lowered = " ".join(str(text or "").lower().split())
    for marker in _LIST_MARKERS:
        position = lowered.find(marker)
        if position == -1:
            continue
        remainder = text[position + len(marker):].strip(" ,.:;?\t\n")
        remainder = re.sub(r"^(?:for|on|this|next)\s*", "", remainder, flags=re.IGNORECASE).strip()
        return remainder
    return ""


def match_items_by_title(
    candidates: "tuple[CalendarItemRecord, ...]", title: str
) -> "tuple[CalendarItemRecord, ...]":
    """Which of ``candidates`` a person naming ``title`` most plausibly
    means - exact match first, then a substring either direction. The
    identical instrument, and the identical honest limitation,
    ``tasks.py``'s own ``match_tasks_by_topic`` already states: not a
    claim of complete natural-language understanding, a real, useful
    deterministic mechanism."""
    title_lower = " ".join(title.strip().lower().split())
    if not title_lower:
        return ()
    exact = tuple(c for c in candidates if c.title.strip().lower() == title_lower)
    if exact:
        return exact
    return tuple(
        c for c in candidates
        if title_lower in c.title.lower() or c.title.lower() in title_lower
    )


# ---------------------------------------------------------------------------
# Building the governed record
# ---------------------------------------------------------------------------


def derive_topic_key(title: str) -> str:
    """The topic this item is grouped under, when no explicit
    personal-context topic was given - derived from its own title with
    the identical normalization ``personal_context.py`` already applies,
    so a calendar item and a personal-context statement about "the same
    thing" land on the same key without either module knowing about the
    other's internals beyond this one shared function."""
    return normalize_topic_key(title)


def build_calendar_item_record(
    candidate: CalendarItemCandidate,
    *,
    agent_name: str,
    topic_key: "str | None" = None,
    series_key: "str | None" = None,
    item_key: "str | None" = None,
    related_task_id: "str | None" = None,
    related_personal_context_topic: "str | None" = None,
) -> MemoryRecord:
    """The MemoryRecord a governed write path retains for one candidate.

    ``item_key`` defaults to a fresh id (a genuinely new item);
    ``series_key`` defaults to that same value (a batch of one - an
    ordinary standalone item is its own, single-member series);
    ``topic_key`` defaults to one derived from the title. A caller
    creating several items from one teaching event passes the *same*
    ``series_key``/``topic_key`` for each - see ``capabilities.py``'s
    own calendar handler.
    """
    resolved_item_key = item_key or uuid.uuid4().hex[:12]
    resolved_series_key = series_key or resolved_item_key
    resolved_topic_key = topic_key or derive_topic_key(candidate.title)

    content = candidate.title
    return MemoryRecord(
        content=content,
        kind=CALENDAR_ITEM_KIND,
        tags={
            CALENDAR_ITEM_TAG,
            _item_tag(resolved_item_key),
            _series_tag(resolved_series_key),
            _topic_tag(resolved_topic_key),
        },
        context={
            "item_key": resolved_item_key,
            "series_key": resolved_series_key,
            "topic_key": resolved_topic_key,
            "title": candidate.title,
            "start_date": candidate.start_date.isoformat() if candidate.start_date else None,
            "start_time": candidate.start_time.isoformat() if candidate.start_time else None,
            "end_time": candidate.end_time.isoformat() if candidate.end_time else None,
            "recurrence": candidate.recurrence.to_dict(),
            "status": candidate.status,
            "notes": candidate.notes,
            "source": "conversation",
            "noted_by": agent_name,
            "related_task_id": related_task_id,
            "related_personal_context_topic": related_personal_context_topic,
        },
    )


# ---------------------------------------------------------------------------
# Retrieval - two-level "latest wins", never a second store.
# ---------------------------------------------------------------------------


def _to_record(raw: MemoryRecord) -> "CalendarItemRecord | None":
    ctx = raw.context or {}
    item_key = ctx.get("item_key")
    if not item_key:
        return None
    start_date_raw = ctx.get("start_date")
    start_time_raw = ctx.get("start_time")
    end_time_raw = ctx.get("end_time")
    return CalendarItemRecord(
        item_id=raw.id,
        item_key=str(item_key),
        series_key=str(ctx.get("series_key") or item_key),
        topic_key=str(ctx.get("topic_key") or ""),
        title=str(ctx.get("title") or raw.content),
        start_date=date.fromisoformat(start_date_raw) if start_date_raw else None,
        start_time=time.fromisoformat(start_time_raw) if start_time_raw else None,
        end_time=time.fromisoformat(end_time_raw) if end_time_raw else None,
        recurrence=Recurrence.from_dict(ctx.get("recurrence")),
        status=str(ctx.get("status") or STATUS_SCHEDULED),
        notes=str(ctx.get("notes") or ""),
        noted_by=str(ctx.get("noted_by") or ""),
        created_at=raw.created_at,
        record_id=raw.id,
        related_task_id=ctx.get("related_task_id"),
        related_personal_context_topic=ctx.get("related_personal_context_topic"),
    )


def current_item_for_key(item_key: str, *, recall: "_Recall | None") -> "CalendarItemRecord | None":
    """The single, latest state of one calendar item - honestly None
    when it has never existed, or no ``recall`` reaches this call."""
    if recall is None:
        return None
    for raw in recall(kind=CALENDAR_ITEM_KIND, tags={_item_tag(item_key)}, limit=1):
        return _to_record(raw)
    return None


def current_series_for_topic(
    topic_key: str, *, recall: "_Recall | None", limit: int = 200
) -> "tuple[CalendarItemRecord, ...]":
    """Every item belonging to ``topic_key``'s own most recent taught
    series - the newest teaching event for this topic, each of its own
    items resolved to *their* own latest state (an individually edited
    item within an otherwise-current series still resolves correctly),
    cancelled items excluded. An older, superseded series is never
    returned here - it still exists in Memory, just not as current."""
    if recall is None:
        return ()
    newest = None
    for raw in recall(kind=CALENDAR_ITEM_KIND, tags={_topic_tag(topic_key)}, limit=1):
        newest = raw
        break
    if newest is None:
        return ()
    series_key = str((newest.context or {}).get("series_key") or "")
    if not series_key:
        return ()

    seen_items: set[str] = set()
    resolved: list[CalendarItemRecord] = []
    for raw in recall(kind=CALENDAR_ITEM_KIND, tags={_series_tag(series_key)}, limit=limit):
        ctx = raw.context or {}
        item_key = str(ctx.get("item_key") or "")
        if not item_key or item_key in seen_items:
            continue
        seen_items.add(item_key)
        current = current_item_for_key(item_key, recall=recall)
        if current is not None and current.status != STATUS_CANCELLED:
            resolved.append(current)
    return tuple(resolved)


def all_current_calendar_items(
    *, recall: "_Recall | None", limit: int = 200
) -> "tuple[CalendarItemRecord, ...]":
    """Every topic's own current series, resolved and flattened - real
    Memory content, never a second index. Mirrors
    ``personal_context.all_current_personal_context``'s own shape, one
    level deeper (topic -> series -> item)."""
    if recall is None:
        return ()
    seen_topics: set[str] = set()
    resolved: list[CalendarItemRecord] = []
    for raw in recall(kind=CALENDAR_ITEM_KIND, tags={CALENDAR_ITEM_TAG}, limit=limit):
        ctx = raw.context or {}
        topic_key = str(ctx.get("topic_key") or "")
        if not topic_key or topic_key in seen_topics:
            continue
        seen_topics.add(topic_key)
        resolved.extend(current_series_for_topic(topic_key, recall=recall, limit=limit))
    return tuple(resolved)


def next_occurrences(
    item: CalendarItemRecord, *, today: date, count: int = 5, horizon_days: int = 60
) -> "tuple[date, ...]":
    """Concrete future dates this item falls on, on or after ``today`` -
    a pure expansion of its recurrence, never stored. A one-off item
    contributes its own ``start_date`` only when that date has not
    already passed; a weekly-recurring item is expanded day by day up to
    ``horizon_days`` ahead (bounded, the same "over-fetch and trim"
    honesty ``day.py``'s own docstring already states for an unbounded
    query), honouring ``recurrence.until`` when one was given.
    """
    if not item.recurrence.is_recurring:
        if item.start_date is not None and item.start_date >= today:
            return (item.start_date,)
        return ()

    found: list[date] = []
    cursor = today
    for _ in range(horizon_days):
        if item.recurrence.until is not None and cursor > item.recurrence.until:
            break
        if cursor.weekday() in item.recurrence.days_of_week:
            found.append(cursor)
            if len(found) >= count:
                break
        cursor += timedelta(days=1)
    return tuple(found)


def find_conflicts(
    candidate: CalendarItemCandidate, existing: "tuple[CalendarItemRecord, ...]", *, today: date,
) -> "tuple[CalendarItemRecord, ...]":
    """Which of ``existing`` genuinely overlaps ``candidate`` in time -
    only ever reported when the available information actually supports
    it (both items name a real date or a shared recurring weekday, and
    both name a start time), never guessed from a bare date-only or
    time-only item."""
    if candidate.start_time is None:
        return ()
    candidate_days: "set[int]"
    if candidate.recurrence.is_recurring:
        candidate_days = set(candidate.recurrence.days_of_week)
        candidate_dates: "set[date]" = set()
    elif candidate.start_date is not None:
        candidate_days = {candidate.start_date.weekday()}
        candidate_dates = {candidate.start_date}
    else:
        return ()

    def _overlaps_time(a_start, a_end, b_start, b_end) -> bool:
        a_end = a_end or a_start
        b_end = b_end or b_start
        return a_start < b_end and b_start < a_end

    conflicts: list[CalendarItemRecord] = []
    for other in existing:
        if other.status == STATUS_CANCELLED or other.start_time is None:
            continue
        if other.recurrence.is_recurring:
            other_days = set(other.recurrence.days_of_week)
            if not (other_days & candidate_days):
                continue
        elif other.start_date is not None:
            if candidate.recurrence.is_recurring:
                if other.start_date.weekday() not in candidate_days:
                    continue
            elif other.start_date not in candidate_dates:
                continue
        else:
            continue
        if _overlaps_time(candidate.start_time, candidate.end_time, other.start_time, other.end_time):
            conflicts.append(other)
    return tuple(conflicts)
